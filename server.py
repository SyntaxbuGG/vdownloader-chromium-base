from collections import defaultdict
import json

import subprocess
import uuid
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware

import os
import logging

from urllib.parse import urlparse, unquote
import asyncio

from urllib.parse import quote


from pydantic_models import DownloadRequest
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles

from hls import get_hls_video_size

from dotenv import load_dotenv
load_dotenv()

mediaStreamExtensions = ['HLS', "DASH"]
user_semaphores = defaultdict(lambda: asyncio.Semaphore(2))

# SAVE_DIR = "downloads"
# THUMB_DIR = "thumbnails"
# os.makedirs(SAVE_DIR, exist_ok=True)
# os.makedirs(THUMB_DIR, exist_ok=True)

logging.basicConfig(level=logging.WARNING)

EXTENSION_ID_STORE = os.environ.get("EXTENSION_ID_STORE")
EXTENSION_ID_LOCAL = os.environ.get("EXTENSION_ID_LOCAL")


app = FastAPI()
# app.mount("/thumbnails", StaticFiles(directory="thumbnails"), name="thumbnails")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
    allow_credentials=False,
)


def get_url_basename(url):
    """Достаём имя файла из URL, если заголовков нет."""
    parsed = urlparse(url)
    filename = os.path.basename(parsed.path)
    filename = unquote(filename)
    return os.path.basename(filename)


def build_ff_headers(headers: dict | None = None):
    if not headers:
        return []
    headers_str = "\r\n".join(f"{k}: {v}" for k, v in headers.items())
    return ["-headers", headers_str]


async def probe_video(filepath_or_url: str, headers: dict | None = None, video_type: str | None = None,):
    """Возвращает размер и длительность видео"""

    def run_ffprobe():
        base_cmd = [
            "ffprobe", "-v", "error",
            "-analyzeduration", "10M",
            "-probesize", "10M",
            "-of", "json"
        ] + build_ff_headers(headers)

        cmd = base_cmd + [
            "-allowed_extensions", "ALL",
            "-show_entries", "format=duration,size,bit_rate",
            filepath_or_url
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0: 
            logging.exception("Ошибка при обработке video_info")  # показывает stack trace
            raise HTTPException(400, "Не удалось получить информацию о видео")
            return json.dumps({"format": {}})

        return result.stdout

    output = await asyncio.to_thread(run_ffprobe)
    info = json.loads(output)
    if video_type in mediaStreamExtensions:
        size = await get_hls_video_size(filepath_or_url)
    else:
        size_raw = info["format"].get("size")
        size = int(size_raw) if size_raw else None

    duration_raw = info["format"].get("duration")
    duration_sec = float(duration_raw) if duration_raw else None

    return size, duration_sec


@app.post("/video_info")
async def video_info(data: DownloadRequest):
    try:
        url = data.url
        req_headers = data.headers or {}
        size, duration = await probe_video(url, req_headers, data.type)

        return {"size": size, "duration_sec": duration}
    except Exception as e:
        logging.warning("Ошибка: %s", e)
        raise HTTPException(400, "Не удалось получить информацию о видео")


async def stream_ffmpeg_output(proc: subprocess.Popen, chunk_size: int = 1024 * 1024):
    """Отдаёт stdout ffmpeg как поток для StreamingResponse"""
    while chunk := await asyncio.to_thread(proc.stdout.read, chunk_size):
        yield chunk


async def run_ffmpeg_process(url: str, headers: dict | None = None) -> subprocess.Popen:

    headers_args = build_ff_headers(headers)
    """Запускает ffmpeg и возвращает subprocess и поток вывода"""
    cmd = [
        "ffmpeg", "-y",
        # "-loglevel", "error",   # отключаем логи
        "-hide_banner",          # убираем баннер версииF
        *headers_args,
        "-i", url,
        "-c:v", "copy",  # видео копируем
        "-c:a", "aac",   # аудио перекодируем в AAC
        "-b:a", "128k",  # битрейт аудио
        "-f", "mpegts",
        # "-movflags", "+faststart",
        # "-movflags", "frag_keyframe+empty_moov+default_base_moof",
        "pipe:1",
    ]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        # stderr=subprocess.PIPE,
        stderr=None,  # для показа в консоль данные
        # stderr=subprocess.DEVNULL,  # 🔇 подавляем ffmpeg логи
        bufsize=0
    )

    return proc


@app.post("/download")
async def start_download(data: DownloadRequest, request: Request):
    user_id = request.client.host
    semaphore = user_semaphores[user_id]
    task_id = str(uuid.uuid4())
    filename = data.filename or "video"
    video_title = filename.replace(' ', "-").lower()[:60]
    out_name = f"{video_title}.mp4"
    safe_name = quote(out_name)

    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{safe_name}",
        "Content-Type": "video/mp4",
        "Cache-Control": "no-cache",
        "X-Task-Id": task_id,
    }
    # 👇 ffmpeg запускается лениво, только когда клиент начнёт читать StreamingResponse

    async def start_stream():
        async with semaphore:
            proc = await run_ffmpeg_process(data.url, data.headers)
            try:
                async for chunk in stream_ffmpeg_output(proc):
                    yield chunk

            except asyncio.CancelledError:
                logging.info(
                    "⬅️ Клиент отменил загрузку — останавливаем FFmpeg")
                try:

                    proc.terminate()
                    await asyncio.wait_for(asyncio.to_thread(proc.wait), timeout=3)
                except asyncio.TimeoutError:
                    proc.kill()
                raise
            except ConnectionResetError:
                logging.info("TCP соединение закрыто клиентом")

            finally:
                # Гарантированное уничтожение процесса
                if proc.poll() is None:
                    try:
                        proc.terminate()
                        await asyncio.wait_for(asyncio.to_thread(proc.wait), timeout=3)
                    except asyncio.TimeoutError:
                        proc.kill()
    return StreamingResponse(start_stream(), headers=headers)
