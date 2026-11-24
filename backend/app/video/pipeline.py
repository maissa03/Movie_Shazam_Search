from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

from app.video.ffmpeg_io import FRAME_SIZE, DecodeStats, FFmpegError, iter_gray_frames
from app.video.fingerprint import phash_gray
from app.video.segmentation import frame_interval_ms

_ABORT_EVENT = None
_BARRIER = None


class SegmentAborted(RuntimeError):
    pass


@dataclass(frozen=True)
class SegmentTask:

    video_path: str
    segment_index: int
    start_ms: int
    end_ms: int
    fps: float
    overlap_ms: int = 0
    frame_size: int = FRAME_SIZE
    ffmpeg_threads: int = 1
    timeout_s: float = 600.0
    ffmpeg_bin: str | None = None
    movie_id: int | None = None
    job_id: str | None = None


@dataclass
class SegmentResult:
    segment_index: int
    start_ms: int
    end_ms: int
    timestamps: list[int] = field(default_factory=list)
    hashes: list[int] = field(default_factory=list)
    informativeness: list[float] = field(default_factory=list)
    frames_decoded: int = 0
    decode_s: float = 0.0
    hash_s: float = 0.0
    wall_s: float = 0.0
    pid: int = 0

    @property
    def count(self) -> int:
        return len(self.hashes)


def analyze_segment(task: SegmentTask) -> SegmentResult:
    t_start = time.perf_counter()
    interval = frame_interval_ms(task.fps)
    overlap = (task.overlap_ms // interval) * interval
    decode_start = max(0, task.start_ms - overlap)
    stats = DecodeStats()
    result = SegmentResult(task.segment_index, task.start_ms, task.end_ms, pid=os.getpid())
    hash_s = 0.0
    for k, frame in iter_gray_frames(
        task.video_path,
        start_ms=decode_start,
        duration_ms=task.end_ms - decode_start,
        fps=task.fps,
        size=task.frame_size,
        threads=task.ffmpeg_threads,
        timeout_s=task.timeout_s,
        stats=stats,
        ffmpeg_bin=task.ffmpeg_bin,
    ):
        if _ABORT_EVENT is not None and k % 16 == 0 and _ABORT_EVENT.is_set():
            raise SegmentAborted("arrêt de l'application demandé")
        ts = decode_start + k * interval
        if ts < task.start_ms:
            continue
        if ts >= task.end_ms:
            break
        t0 = time.perf_counter()
        value, info = phash_gray(frame)
        hash_s += time.perf_counter() - t0
        result.timestamps.append(ts)
        result.hashes.append(value)
        result.informativeness.append(round(info, 3))
    result.frames_decoded = stats.frames
    result.decode_s = stats.read_s
    result.hash_s = hash_s
    result.wall_s = time.perf_counter() - t_start
    if not result.hashes and task.end_ms - task.start_ms >= interval:
        raise FFmpegError(f"aucune image décodée entre {task.start_ms} et {task.end_ms} ms "
                          "(fichier tronqué ou corrompu ?)")
    return result


def worker_initializer(abort_event=None, barrier=None) -> None:  # noqa: ANN001
    global _ABORT_EVENT, _BARRIER
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")
    import cv2

    cv2.setNumThreads(1)
    _ABORT_EVENT = abort_event
    _BARRIER = barrier


def worker_ping(wait_barrier: bool = False) -> int:
    if wait_barrier and _BARRIER is not None:
        _BARRIER.wait(timeout=120)
    return os.getpid()
