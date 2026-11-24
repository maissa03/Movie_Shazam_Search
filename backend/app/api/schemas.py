from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, PlainSerializer

from app.db.models import Movie, Segment

UtcDateTime = Annotated[
    datetime, PlainSerializer(lambda d: d.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z"), return_type=str)
]

BROWSER_CODECS = {"h264", "vp8", "vp9", "av1"}
BROWSER_CONTAINERS = ("mp4", "mov", "webm", "matroska")


class MovieOut(BaseModel):
    id: int
    title: str
    year: int | None
    original_filename: str
    file_size: int
    duration_ms: int
    width: int
    height: int
    codec: str
    fps: float
    format_name: str
    status: str
    index_version: str | None
    segments_total: int
    segments_done: int
    fingerprints_count: int
    has_thumbnail: bool
    playable_in_browser: bool
    error: str | None
    created_at: UtcDateTime
    indexed_at: UtcDateTime | None
    active_job_id: str | None = None

    @classmethod
    def from_model(cls, m: Movie, active_job_id: str | None = None) -> MovieOut:
        playable = m.codec in BROWSER_CODECS and any(c in m.format_name for c in BROWSER_CONTAINERS)
        return cls(id=m.id, title=m.title, year=m.year, original_filename=m.original_filename,
                   file_size=m.file_size, duration_ms=m.duration_ms, width=m.width, height=m.height, codec=m.codec,
                   fps=m.fps, format_name=m.format_name, status=m.status, index_version=m.index_version,
                   segments_total=m.segments_total, segments_done=m.segments_done,
                   fingerprints_count=m.fingerprints_count, has_thumbnail=bool(m.thumbnail_name),
                   playable_in_browser=playable, error=m.error, created_at=m.created_at, indexed_at=m.indexed_at,
                   active_job_id=active_job_id)


class SegmentOut(BaseModel):
    id: int
    segment_index: int
    start_ms: int
    end_ms: int
    status: str
    fingerprints_count: int
    frames_decoded: int
    processing_ms: int | None
    worker_pid: int | None
    error: str | None
    index_version: str

    @classmethod
    def from_model(cls, s: Segment) -> SegmentOut:
        return cls(id=s.id, segment_index=s.segment_index, start_ms=s.start_ms, end_ms=s.end_ms, status=s.status,
                   fingerprints_count=s.fingerprints_count, frames_decoded=s.frames_decoded,
                   processing_ms=s.processing_ms, worker_pid=s.worker_pid, error=s.error,
                   index_version=s.index_version)


class MovieDetailOut(MovieOut):
    segments: list[SegmentOut] = Field(default_factory=list)


class JobOut(BaseModel):
    id: str
    kind: str
    label: str
    movie_id: int | None
    state: str
    stage: str | None
    completed_units: int
    total_units: int
    progress: float
    fingerprints: int
    frames_decoded: int
    workers_used: int
    worker_pids: list[int]
    created_at: str
    started_at: str | None
    finished_at: str | None
    elapsed_ms: int | None
    error: str | None
    cancel_requested: bool
    search_id: str | None
    details: dict[str, Any]


class MovieUpdateIn(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=300)
    year: int | None = Field(None, ge=1870, le=2100)
    clear_year: bool = Field(False, description="vrai pour effacer l'année")


class ImportOut(BaseModel):
    movie: MovieOut
    job: JobOut | None
    created: bool
    message: str


class ReindexOut(BaseModel):
    movie: MovieOut
    job: JobOut | None
    message: str


class DeleteOut(BaseModel):
    deleted: bool
    movie_id: int
    files_removed: list[str]


class SearchOut(BaseModel):
    id: str
    job_id: str
    status: str
    clip_name: str
    method: str
    query_duration_ms: int | None
    best_movie_id: int | None
    start_ms: int | None
    end_ms: int | None
    score: float | None
    elapsed_ms: int | None
    created_at: UtcDateTime
    result: dict[str, Any]
    job: JobOut | None = None


class SearchStartOut(BaseModel):
    search_id: str
    job: JobOut


class BenchmarkRunIn(BaseModel):
    modes: list[int] = Field(default_factory=lambda: [0, 1, 2, 4],
                             description="0 = séquentiel ; N = pool de N processus")
    repeats: int = Field(1, ge=1, le=5)
    movie_ids: list[int] | None = Field(None, description="films à utiliser (défaut : tous les films indexés)")
    include_recall: bool = True


class HealthOut(BaseModel):
    status: Literal["ok", "degraded"]
    app: str
    index_version: str
    database: dict[str, Any]
    ffmpeg: dict[str, Any]
    concurrency: dict[str, Any]
    limits: dict[str, Any]
    python: str
    platform: str
