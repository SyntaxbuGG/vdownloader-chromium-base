from pydantic import BaseModel


class DownloadRequest(BaseModel):
    url: str
    filename: str | None = None
    size: int | None = None
    type: str | None = None
    duration_sec: float | None = None
    headers: dict[str, str] | None = None


class BulkProgressRequest(BaseModel):
    task_ids: list[str]
