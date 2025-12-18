import httpx
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlparse
import asyncio

async def parse_dash_mpd(mpd_url: str) -> tuple[float, int | None]:
    """
    Возвращает (duration_sec, size_bytes) для VOD DASH (.mpd)
    size_bytes = None если live поток или ошибка
    """
    duration_sec = 0.0
    size_bytes: int | None = 0

    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            r = await client.get(mpd_url)
            if r.status_code != 200:
                return 0.0, None

            root = ET.fromstring(r.text)
            namespace = {'mpd': 'urn:mpeg:dash:schema:mpd:2011'}

            base_url = mpd_url.rsplit("/", 1)[0] + "/"

            segments = []

            # Проходим по всем Representation
            for rep in root.findall(".//mpd:Representation", namespace):
                # SegmentURL если есть
                for seg in rep.findall(".//mpd:SegmentURL", namespace):
                    media = seg.attrib.get("media")
                    if media:
                        segments.append(urljoin(base_url, media))

                # SegmentTemplate: генерируем ссылки
                seg_template = rep.find(".//mpd:SegmentTemplate", namespace)
                if seg_template is not None:
                    media = seg_template.attrib.get("media")
                    timescale = int(seg_template.attrib.get("timescale", "1"))
                    duration = int(seg_template.attrib.get("duration", "0"))
                    start_number = int(seg_template.attrib.get("startNumber", "1"))

                    # Простейшее: генерируем первые N сегментов
                    # Для точного расчета нужно SegmentTimeline
                    if media and duration > 0:
                        segment_count = 1  # Минимум один сегмент
                        for i in range(start_number, start_number + segment_count):
                            url = media.replace("$Number$", str(i))
                            segments.append(urljoin(base_url, url))

            if not segments:
                return 0.0, None  # live поток или пусто

            # Получаем duration и size
            total_size = 0
            for seg in segments:
                try:
                    head = await client.head(seg)
                    total_size += int(head.headers.get("Content-Length", 0))
                except Exception:
                    size_bytes = None
                    break
            else:
                size_bytes = total_size

            # duration = сумма segment durations, если есть SegmentTimeline
            # Для простого случая можно взять duration MPD
            mpd_duration = root.attrib.get("mediaPresentationDuration")
            if mpd_duration:
                # PT1H2M3.5S -> конвертируем в секунды
                import re
                pattern = re.compile(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?')
                m = pattern.match(mpd_duration)
                if m:
                    h = float(m.group(1) or 0)
                    m_ = float(m.group(2) or 0)
                    s = float(m.group(3) or 0)
                    duration_sec = h * 3600 + m_ * 60 + s

    except Exception:
        return 0.0, None

    return duration_sec, size_bytes

# Пример вызова
async def main():
    duration, size = await parse_dash_mpd("https://example.com/video.mpd")
    print("Duration:", duration)
    print("Size:", size)

# asyncio.run(main())
