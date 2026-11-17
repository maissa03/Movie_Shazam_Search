"""Indexation d'un film : même logique métier en mode **séquentiel** et en mode **parallèle**.

* ``plan_for`` : découpage déterministe (indépendant du nombre de processus) ;
* ``build_task`` : description sérialisable d'un segment ;
* ``index_movie_sync`` : version synchrone utilisée par les scripts, les tests et le benchmark.
  - séquentiel : ``analyze_segment`` appelée dans une simple boucle (chemin de référence) ;
  - parallèle : les mêmes ``SegmentTask`` soumises au ``LocalProcessPool``.
  Le gestionnaire de jobs à threads (``concurrency/job_manager.py``) réutilise exactement
  ``plan_for``, ``build_task``, ``analyze_segment`` et les fonctions de ``db/repo.py``.
"""

from __future__ import annotations

import time
from concurrent.futures import FIRST_COMPLETED, Future, wait
from dataclasses import dataclass, field
from pathlib import Path

from app.concurrency.process_pool import LocalProcessPool
from app.core.settings import Settings
from app.db import repo
from app.db.writer import DbWriter
from app.video.ffmpeg_io import find_binary
from app.video.pipeline import SegmentResult, SegmentTask, analyze_segment
from app.video.segmentation import SegmentPlan, choose_segment_ms, plan_segments


def plan_for(duration_ms: int, settings: Settings) -> tuple[int, list[SegmentPlan]]:
    seg_ms = choose_segment_ms(duration_ms, settings.fingerprint_fps, settings.segment_duration_seconds,
                               settings.min_segment_seconds, settings.target_min_segments)
    return seg_ms, plan_segments(duration_ms, seg_ms)


def build_task(video_path: Path, todo: repo.SegmentTodo | SegmentPlan, settings: Settings,
               movie_id: int | None = None, job_id: str | None = None) -> SegmentTask:
    index = todo.segment_index if isinstance(todo, repo.SegmentTodo) else todo.index
    return SegmentTask(
        video_path=str(video_path), segment_index=index, start_ms=todo.start_ms, end_ms=todo.end_ms,
        fps=settings.fingerprint_fps, overlap_ms=settings.segment_overlap_ms,
        ffmpeg_threads=settings.ffmpeg_threads, timeout_s=settings.segment_timeout_seconds,
        ffmpeg_bin=find_binary("ffmpeg"), movie_id=movie_id, job_id=job_id,
    )


@dataclass
class IndexReport:
    movie_id: int
    ok: bool
    message: str
    segments_total: int = 0
    segments_computed: int = 0
    fingerprints: int = 0
    frames_decoded: int = 0
    elapsed_s: float = 0.0
    pids: set[int] = field(default_factory=set)


def index_movie_sync(movie_id: int, video_path: Path, duration_ms: int, settings: Settings,
                     writer: DbWriter, pool: LocalProcessPool | None = None, force: bool = False,
                     job_id: str | None = None) -> IndexReport:
    """Indexe un film de bout en bout (bloquant).  ``pool=None`` -> mode séquentiel."""
    t0 = time.perf_counter()
    version = settings.index_version
    seg_ms, plans = plan_for(duration_ms, settings)
    todo = writer.run_sync(lambda s: repo.prepare_index(s, movie_id, version, seg_ms, plans, force))
    report = IndexReport(movie_id, False, "", segments_total=len(plans))
    if todo is None:
        report.message = "film introuvable"
        return report
    tasks = {t.segment_id: build_task(video_path, t, settings, movie_id, job_id) for t in todo}
    errors: list[str] = []

    def _save(seg_id: int, res: SegmentResult) -> None:
        writer.run_sync(lambda s: repo.save_segment_result(s, movie_id, seg_id, version, res, job_id))
        report.segments_computed += 1
        report.frames_decoded += res.frames_decoded
        report.pids.add(res.pid)

    if pool is None:  # ---------------- séquentiel : une simple boucle
        for seg_id, task in tasks.items():
            try:
                _save(seg_id, analyze_segment(task))
            except Exception as exc:  # noqa: BLE001
                errors.append(f"segment {task.segment_index}: {exc}")
                writer.run_sync(lambda s, sid=seg_id, e=str(exc): repo.mark_segment_failed(s, sid, e))
                break
    else:  # ---------------------------- parallèle : mêmes tâches, fenêtre bornée de futures
        window = pool.workers * settings.max_inflight_segments_per_worker
        queue = list(tasks.items())
        pending: dict[Future, int] = {}
        while queue or pending:
            while queue and len(pending) < window and not errors:
                seg_id, task = queue.pop(0)
                pending[pool.submit(analyze_segment, task)] = seg_id
            if not pending:
                break
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            for fut in done:
                seg_id = pending.pop(fut)
                try:
                    _save(seg_id, fut.result())
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"segment {tasks[seg_id].segment_index}: {exc}")
                    writer.run_sync(lambda s, sid=seg_id, e=str(exc): repo.mark_segment_failed(s, sid, e))
            if errors:
                for fut in pending:
                    fut.cancel()  # les segments non démarrés ne seront jamais lancés
                queue.clear()

    if errors:
        writer.run_sync(lambda s: repo.update_movie(s, movie_id, status="failed", error="; ".join(errors)[:2000]))
        report.message = "; ".join(errors)
    else:
        ok, msg = writer.run_sync(lambda s: repo.finalize_index(s, movie_id, version))
        report.ok, report.message = ok, msg
    report.fingerprints = writer.run_sync(lambda s: (repo.get_movie(s, movie_id) or _Zero).fingerprints_count)
    report.elapsed_s = time.perf_counter() - t0
    return report


class _Zero:
    fingerprints_count = 0
