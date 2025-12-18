from urllib.parse import urljoin
import httpx
import re


async def get_hls_video_size(url: str) -> tuple[int | None, float | None]:
    async with httpx.AsyncClient(http2=True) as client:
        resp = await client.get(url)
        if resp.status_code != 200:
            return None,None

        lines = resp.text.splitlines()
        total_duration = 0.0
        bitrate = None
        first_segment_url = None
        first_segment_duration = None

        for i, line in enumerate(lines):
            line = line.strip()

            # ищем BANDWIDTH (битрейт)
            if line.startswith("#EXT-X-STREAM-INF"):
                match = re.search(r"BANDWIDTH=(\d+)", line)
                if match:
                    bitrate = int(match.group(1))  # бит/сек

            # ищем EXTINF (длительность сегмен+та)
            if line.startswith("#EXTINF:"):
                try:
                    dur = float(line.split(":")[1].strip().strip(","))
                    total_duration += dur
                    if first_segment_duration is None:
                        first_segment_duration = dur
                        # следующий после EXTINF — это URL сегмента
                        if i + 1 < len(lines):
                            first_segment_url = urljoin(
                                url, lines[i + 1].strip())
                except Exception:
                    continue

        # если нет длительности — не сможем посчитать
        if total_duration == 0:
            return None, None
        if bitrate:
            total_size = int(bitrate * total_duration / 8)  # байты
            return total_size, total_duration

        # если битрейт не указан — пробуем вычислить по первому сегменту
        if first_segment_url and first_segment_duration:
            head = await client.head(first_segment_url, follow_redirects=True)
            size = int(head.headers.get("Content-Length", 0)) or None
            return size, total_duration

        return None,total_duration  


# async def fetch_urls(line: str, url: str, client: httpx.AsyncClient) -> int:
#     segment_url = ""
#     segment_url = urljoin(url, line)
#     head = await client.head(segment_url, follow_redirects=True)
#     c1 = head.headers.get("Content-Length")
#     if c1:
#         return int(c1)
#     return 0


# async def get_hls_video_size1(url: str) -> int | None:
#     async with httpx.AsyncClient(http2=True) as client:
#         resp = await client.get(url)
#         if resp.status_code != 200:
#             return

#         total_size = 0
#         segments_url = [fetch_urls(line, url, client)
#                         for line in resp.text.splitlines() if line.strip() and not line.startswith("#")]
#         results = await gather(*segments_url)
#         total_size = sum(results)
#         return total_size if total_size > 0 else None
