from __future__ import annotations

from pathlib import Path

from app.core.settings import Settings
from app.db import repo
from app.video.ffmpeg_io import find_binary
from app.video.pipeline import SegmentTask
from app.video.segmentation import SegmentPlan, choose_segment_ms, plan_segments


def plan_for(duration_ms: int, settings: Settings) -> tuple[int, list[SegmentPlan]]:
    """Découpe un film en segments : chaque segment est une unité de travail pour un processus du pool."""
    seg_ms = choose_segment_ms(duration_ms, settings.fingerprint_fps, settings.segment_duration_seconds,
                               settings.min_segment_seconds, settings.target_min_segments)
    return seg_ms, plan_segments(duration_ms, seg_ms)


def build_task(video_path: Path, todo: repo.SegmentTodo, settings: Settings, movie_id: int,
               job_id: str) -> SegmentTask:
    """Construit la tâche (sérialisable par pickle) envoyée à un processus worker par apply_async()."""
    return SegmentTask(
        video_path=str(video_path), segment_index=todo.segment_index, start_ms=todo.start_ms, end_ms=todo.end_ms,
        fps=settings.fingerprint_fps, overlap_ms=settings.segment_overlap_ms,
        ffmpeg_threads=settings.ffmpeg_threads, timeout_s=settings.segment_timeout_seconds,
        ffmpeg_bin=find_binary("ffmpeg"), movie_id=movie_id, job_id=job_id,
    )
