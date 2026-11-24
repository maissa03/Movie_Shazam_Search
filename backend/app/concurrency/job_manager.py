from __future__ import annotations

import json
import logging
import queue
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.concurrency.process_pool import SegmentWork, wait_ready
from app.db import repo
from app.db.models import utcnow
from app.db.search_repo import find_candidates
from app.services import library, searching
from app.services.indexing import build_task, plan_for
from app.video.ffmpeg_io import FFmpegError
from app.video.matching import identify
from app.video.segmentation import format_hms, frame_interval_ms

if TYPE_CHECKING:
    from app.main import AppContext

log = logging.getLogger("movieshazam.jobs")
TERMINAL = ("completed", "failed", "cancelled")


class QueueFull(RuntimeError):
    pass


class NotAccepting(RuntimeError):
    pass


class JobCancelled(Exception):
    pass


@dataclass
class JobState:

    id: str
    kind: str
    label: str
    movie_id: int | None = None
    state: str = "queued"
    stage: str = "en file d'attente"
    completed_units: int = 0
    total_units: int = 0
    fingerprints: int = 0
    frames_decoded: int = 0
    pids: set[int] = field(default_factory=set)
    created_at: datetime = field(default_factory=utcnow)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    cancel_requested: bool = False
    force: bool = False
    search_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    done: threading.Event = field(default_factory=threading.Event, repr=False)

    @property
    def progress(self) -> float:
        if self.total_units:
            return self.completed_units / self.total_units
        return 1.0 if self.state == "completed" else 0.0

    def to_dict(self) -> dict:
        end = self.finished_at or utcnow()
        return {
            "id": self.id, "kind": self.kind, "label": self.label, "movie_id": self.movie_id, "state": self.state,
            "stage": self.stage, "completed_units": self.completed_units, "total_units": self.total_units,
            "progress": round(self.progress, 4), "fingerprints": self.fingerprints,
            "frames_decoded": self.frames_decoded, "workers_used": len(self.pids), "worker_pids": sorted(self.pids),
            "created_at": self.created_at.isoformat() + "Z",
            "started_at": self.started_at.isoformat() + "Z" if self.started_at else None,
            "finished_at": self.finished_at.isoformat() + "Z" if self.finished_at else None,
            "elapsed_ms": int((end - self.started_at).total_seconds() * 1000) if self.started_at else None,
            "error": self.error, "cancel_requested": self.cancel_requested, "search_id": self.search_id,
            "details": dict(self.details),
        }


def job_row_to_dict(row) -> dict:  # noqa: ANN001
    pids = json.loads(row.worker_pids or "[]")
    elapsed = None
    if row.started_at:
        elapsed = int(((row.finished_at or utcnow()) - row.started_at).total_seconds() * 1000)
    details = json.loads(row.details_json or "{}")
    return {
        "id": row.id, "kind": row.kind, "label": row.label, "movie_id": row.movie_id, "state": row.state,
        "stage": row.stage, "completed_units": row.completed_units, "total_units": row.total_units,
        "progress": round(row.completed_units / row.total_units, 4) if row.total_units else
        (1.0 if row.state == "completed" else 0.0),
        "fingerprints": row.fingerprints_count, "frames_decoded": row.frames_decoded,
        "workers_used": len(pids), "worker_pids": pids,
        "created_at": row.created_at.isoformat() + "Z",
        "started_at": row.started_at.isoformat() + "Z" if row.started_at else None,
        "finished_at": row.finished_at.isoformat() + "Z" if row.finished_at else None,
        "elapsed_ms": elapsed, "error": row.error, "cancel_requested": False,
        "search_id": details.get("search_id"), "details": details,
    }


class JobManager:
    def __init__(self, ctx: AppContext):
        s = ctx.settings
        self.ctx = ctx
        self._lock = threading.Lock()
        self._jobs: dict[str, JobState] = {}
        self._movie_jobs: dict[int, str] = {}
        self._accepting = False
        self._index_queue: queue.Queue[str | None] = queue.Queue(maxsize=s.max_queued_jobs)
        self._index_threads: list[threading.Thread] = []
        self._search_pool: ThreadPoolExecutor | None = None

    def start(self) -> None:
        s = self.ctx.settings
        self._search_pool = ThreadPoolExecutor(max_workers=s.max_active_search_jobs, thread_name_prefix="search")
        self._index_threads = [
            threading.Thread(target=self._index_worker, daemon=False,
                             name="index-coordinator" if i == 0 else f"index-coordinator-{i}")
            for i in range(s.max_active_index_jobs)
        ]
        for t in self._index_threads:
            t.start()
        with self._lock:
            self._accepting = True

    def stop(self, timeout: float = 60.0) -> None:
        with self._lock:
            self._accepting = False
            for job in self._jobs.values():
                if job.state not in TERMINAL:
                    job.cancel_requested = True
        while True:
            try:
                job_id = self._index_queue.get_nowait()
            except queue.Empty:
                break
            job = self._get(job_id) if job_id else None
            if job is not None:
                self._cancel_queued(job, "arrêt de l'application")
            self._index_queue.task_done()
        for _ in self._index_threads:
            self._index_queue.put(None)
        deadline = time.monotonic() + timeout
        for t in self._index_threads:
            t.join(max(0.0, deadline - time.monotonic()))
        if self._search_pool is not None:
            self._search_pool.shutdown(wait=True, cancel_futures=True)
        for job in self._live_jobs():
            if job.kind == "search":
                self._search_failed(job, "cancelled", "arrêt de l'application")

    def join(self, timeout: float = 30.0) -> None:
        deadline = time.monotonic() + timeout
        for t in self._index_threads:
            t.join(max(0.0, deadline - time.monotonic()))
        alive = [t.name for t in self._index_threads if t.is_alive()]
        if alive:
            log.warning("threads encore actifs à l'arrêt : %s", alive)

    @property
    def accepting(self) -> bool:
        with self._lock:
            return self._accepting

    def _get(self, job_id: str) -> JobState | None:
        with self._lock:
            return self._jobs.get(job_id)

    def _live_jobs(self) -> list[JobState]:
        with self._lock:
            return [j for j in self._jobs.values() if j.state not in TERMINAL]

    def snapshot(self, job_id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.to_dict() if job else None

    def snapshots(self, active_only: bool = False) -> list[dict]:
        with self._lock:
            return [j.to_dict() for j in self._jobs.values() if not active_only or j.state not in TERMINAL]

    def active_job_id(self, movie_id: int) -> str | None:
        with self._lock:
            return self._movie_jobs.get(movie_id)

    def _cancelled(self, job: JobState) -> bool:
        with self._lock:
            return job.cancel_requested

    def _row_fields(self, job: JobState) -> dict:  # appelé sous self._lock
        return dict(kind=job.kind, movie_id=job.movie_id, label=job.label[:300], state=job.state, stage=job.stage,
                    completed_units=job.completed_units, total_units=job.total_units,
                    fingerprints_count=job.fingerprints, frames_decoded=job.frames_decoded,
                    workers_used=len(job.pids), worker_pids=json.dumps(sorted(job.pids)),
                    created_at=job.created_at, started_at=job.started_at, finished_at=job.finished_at,
                    error=job.error, details_json=json.dumps({**job.details, "search_id": job.search_id}))

    def _enqueue_row(self, job: JobState) -> Future:  # appelé sous self._lock
        fields = self._row_fields(job)
        return self.ctx.writer.submit(lambda s, jid=job.id, f=fields: repo.upsert_job(s, jid, **f))

    def _persist(self, job: JobState) -> None:
        with self._lock:
            fut = self._enqueue_row(job)
        fut.result(timeout=120)

    def _start(self, job: JobState, stage: str) -> bool:
        with self._lock:
            if job.state in TERMINAL or job.cancel_requested:
                return False
            job.state, job.stage, job.started_at = "running", stage, utcnow()
        self._persist(job)
        return True

    def _finish(self, job: JobState, state: str, error: str | None = None, stage: str | None = None) -> bool:
        fut: Future | None = None
        with self._lock:
            if job.state in TERMINAL:
                return False
            job.state = state
            job.error = error
            job.stage = stage or {"completed": "terminé", "failed": "échec", "cancelled": "annulé"}[state]
            job.finished_at = utcnow()
            if job.movie_id is not None and self._movie_jobs.get(job.movie_id) == job.id:
                del self._movie_jobs[job.movie_id]
            try:
                fut = self._enqueue_row(job)
            except Exception:  
                log.exception("persistance du job impossible", extra={"job_id": job.id})
        try:
            if fut is not None:
                fut.result(timeout=120)
        except Exception:  # noqa: BLE001
            log.exception("persistance du job impossible", extra={"job_id": job.id})
        finally:
            job.done.set()
        self._prune()
        return True

    def _prune(self, keep: int = 300) -> None:
        with self._lock:
            finished = [j for j in self._jobs.values() if j.state in TERMINAL]
            for j in sorted(finished, key=lambda j: j.finished_at or utcnow())[:-keep]:
                self._jobs.pop(j.id, None)

    def submit_index(self, movie_id: int, label: str, force: bool = False) -> tuple[dict, bool]:
        with self._lock:
            if not self._accepting:
                raise NotAccepting("l'application s'arrête : nouveaux jobs refusés")
            existing = self._movie_jobs.get(movie_id)
            if existing is not None:
                return self._jobs[existing].to_dict(), False
            if self._index_queue.full():
                raise QueueFull("file d'attente pleine, réessayez plus tard")
            job = JobState(id=uuid.uuid4().hex, kind="index", label=label, movie_id=movie_id, force=force)
            self._jobs[job.id] = job
            self._movie_jobs[movie_id] = job.id
        self._persist(job)
        try:
            self._index_queue.put_nowait(job.id)
        except queue.Full as exc:
            self._finish(job, "failed", "file d'attente pleine")
            raise QueueFull("file d'attente pleine, réessayez plus tard") from exc
        log.info("job %s mis dans la file d'indexation (film « %s »)", job.id[:8], label,
                 extra={"job_id": job.id, "movie_id": movie_id})
        return self.snapshot(job.id) or {}, True

    def submit_search(self, clip_path: Path, clip_name: str, method: str) -> dict:
        with self._lock:
            if not self._accepting or self._search_pool is None:
                raise NotAccepting("l'application s'arrête")
            job = JobState(id=uuid.uuid4().hex, kind="search", label=clip_name)
            job.search_id = job.id
            self._jobs[job.id] = job
        self._persist(job)
        self.ctx.writer.run_sync(lambda s: repo.save_search(s, id=job.id, job_id=job.id, status="queued",
                                                            clip_name=clip_name[:300], method=method))
        try:
            self._search_pool.submit(self._run_search, job, clip_path, clip_name, method)
        except RuntimeError as exc:
            self._search_failed(job, "cancelled", "arrêt de l'application")
            raise NotAccepting("l'application s'arrête") from exc
        return self.snapshot(job.id) or {}

    def cancel(self, job_id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if job.state in TERMINAL:
                return job.to_dict()
            job.cancel_requested = True
            queued_index = job.kind == "index" and job.state == "queued"
            if not queued_index:
                job.stage = "annulation demandée (les segments déjà engagés se terminent)"
        if queued_index:
            self._cancel_queued(job)
        return self.snapshot(job_id)

    def wait_until_terminal(self, job_id: str, timeout: float = 60.0) -> dict | None:
        job = self._get(job_id)
        if job is None:
            return None
        job.done.wait(timeout)
        return self.snapshot(job_id)

    def _index_worker(self) -> None:
        # Consommateur : chaque thread coordinateur retire un job_id de la file (bloquant) et l'indexe.
        # La sentinelle None (déposée par stop()) termine la boucle.
        while True:
            job_id = self._index_queue.get()
            try:
                if job_id is None:
                    return
                job = self._get(job_id)
                if job is None:
                    continue
                with self._lock:
                    skip, cancelled = job.state in TERMINAL, job.cancel_requested
                if skip:
                    continue
                if cancelled:
                    self._cancel_queued(job)
                    continue
                try:
                    self._run_index(job)
                except Exception as exc:  # noqa: BLE001 - jamais de job bloqué en « running »
                    log.exception("indexation en échec", extra={"job_id": job.id, "movie_id": job.movie_id})
                    self._fail_movie(job, f"erreur inattendue : {exc}")
            finally:
                self._index_queue.task_done()

    def _cancel_queued(self, job: JobState, reason: str | None = None) -> None:
        if not self._finish(job, "cancelled", reason):
            return
        mid = job.movie_id
        try:
            self.ctx.writer.run_sync(lambda s: repo.update_movie(s, mid, status="cancelled")
                                     if (m := repo.get_movie(s, mid)) and m.status != "indexed" else None)
        except Exception:  # noqa: BLE001
            log.exception("mise à jour du film impossible", extra={"job_id": job.id, "movie_id": mid})

    def _fail_movie(self, job: JobState, message: str) -> None:
        mid = job.movie_id
        try:
            self.ctx.writer.run_sync(lambda s: repo.update_movie(s, mid, status="failed", error=message[:2000]))
        except Exception:  # noqa: BLE001
            log.exception("mise à jour du film impossible", extra={"job_id": job.id, "movie_id": mid})
        finally:
            self._finish(job, "failed", message)

    def _run_index(self, job: JobState) -> None:
        ctx, s = self.ctx, self.ctx.settings
        mid = job.movie_id
        assert mid is not None
        version = s.index_version
        movie = ctx.read(lambda ss: repo.get_movie(ss, mid))
        if movie is None:
            self._finish(job, "failed", "film supprimé avant l'indexation")
            return
        try:
            path = library.movie_path(movie, s)
        except ValueError:
            path = None
        if path is None or not path.exists():
            self._fail_movie(job, "fichier vidéo introuvable dans le stockage")
            return
        if not self._start(job, "préparation des segments"):
            self._cancel_queued(job)
            return
        seg_ms, plans = plan_for(movie.duration_ms, s)
        todo = ctx.writer.run_sync(lambda ss: repo.prepare_index(ss, mid, version, seg_ms, plans, job.force))
        if todo is None:
            self._finish(job, "failed", "film supprimé pendant la préparation")
            return
        refreshed = ctx.read(lambda ss: repo.get_movie(ss, mid))
        with self._lock:
            job.total_units = len(plans)
            job.completed_units = len(plans) - len(todo)
            job.fingerprints = refreshed.fingerprints_count if refreshed else 0
            job.details.update({"segment_ms": seg_ms, "segments_reused": job.completed_units,
                                "process_workers": ctx.pool.workers, "index_version": version})
            job.stage = f"empreintes : {len(todo)} segment(s) à calculer sur {ctx.pool.workers} processus"
        self._persist(job)
        log.info("[JOB] film « %s » pris en charge par le thread %s (job %s) : %d segment(s) à calculer",
                 movie.title, threading.current_thread().name, job.id[:8], len(todo),
                 extra={"job_id": job.id, "movie_id": mid})

        backlog = [(t, build_task(path, t, s, mid, job.id)) for t in todo]
        # Fenêtre glissante : le coordinateur soumet jusqu'à `window` segments (apply_async, non bloquant)
        # avant d'attendre le premier résultat ; chaque résultat libère une place pour le segment suivant.
        window = ctx.pool.workers * s.max_inflight_segments_per_worker
        pending: dict[SegmentWork, repo.SegmentTodo] = {}
        error: str | None = None
        t0 = time.perf_counter()
        while backlog or pending:
            while backlog and len(pending) < window and error is None and not self._cancelled(job):
                todo_item, task = backlog.pop(0)
                pending[ctx.pool.apply_async(task)] = todo_item
            if not pending:
                break
            for work in wait_ready(pending):
                t = pending.pop(work)
                try:
                    res = work.get()
                except Exception as exc:  
                    if self._cancelled(job):
                        continue
                    msg = (f"segment {t.segment_index} ({format_hms(t.start_ms)}–{format_hms(t.end_ms)}) : "
                           f"{exc}")
                    error = error or msg
                    log.error("segment en échec : %s", exc, extra={"job_id": job.id, "movie_id": mid,
                                                                   "segment": t.segment_index})
                    ctx.writer.run_sync(lambda ss, sid=t.segment_id, m=msg: repo.mark_segment_failed(ss, sid, m))
                    continue
                saved = ctx.writer.run_sync(
                    lambda ss, r=res, sid=t.segment_id: repo.save_segment_result(ss, mid, sid, version, r, job.id))
                if not saved:
                    error = error or "film supprimé pendant l'indexation"
                    continue
                with self._lock:
                    job.completed_units += 1
                    job.fingerprints += res.count
                    job.frames_decoded += res.frames_decoded
                    job.pids.add(res.pid)
                    done, total = job.completed_units, job.total_units
                log.info("[SEGMENT] segment %d terminé par le processus PID %d (%d empreintes en %.1f s) — %d/%d",
                         t.segment_index, res.pid, res.count, res.wall_s, done, total,
                         extra={"job_id": job.id, "movie_id": mid, "segment": t.segment_index, "pid": res.pid})
            if error or self._cancelled(job):
                backlog.clear()
        with self._lock:
            job.details["compute_s"] = round(time.perf_counter() - t0, 3)

        if error:
            self._fail_movie(job, error)
        elif self._cancelled(job):
            ctx.writer.run_sync(lambda ss: repo.update_movie(
                ss, mid, status="cancelled", error="indexation annulée (segments terminés conservés)"))
            self._finish(job, "cancelled")
        else:
            ok, msg = ctx.writer.run_sync(lambda ss: repo.finalize_index(ss, mid, version))
            if ok:
                self._finish(job, "completed")
                with self._lock:
                    done, total, n_fp = job.completed_units, job.total_units, job.fingerprints
                log.info("[FIN] indexation terminée : « %s », %d/%d segments terminés, %d empreintes (job %s)",
                         movie.title, done, total, n_fp, job.id[:8], extra={"job_id": job.id, "movie_id": mid})
            else:
                self._finish(job, "failed", msg)

    def _run_search(self, job: JobState, clip_path: Path, clip_name: str, method: str) -> None:
        ctx, s = self.ctx, self.ctx.settings
        timings: dict[str, float] = {}
        t_all = time.perf_counter()

        def stage(name: str, label: str, fn: Callable[[], Any]) -> Any:
            if self._cancelled(job):
                raise JobCancelled
            with self._lock:
                job.stage = label
            t = time.perf_counter()
            out = fn()
            timings[name] = round((time.perf_counter() - t) * 1000, 1)
            with self._lock:
                job.completed_units += 1
            return out

        try:
            with self._lock:
                job.total_units = 4
            if not self._start(job, "validation du clip"):
                raise JobCancelled
            ctx.writer.run_sync(lambda ss: repo.save_search(ss, id=job.id, job_id=job.id, status="running",
                                                            clip_name=clip_name[:300], method=method))
            info = stage("validation_ms", "validation du clip", lambda: searching.validate_clip(clip_path, s))
            clip = stage("fingerprint_ms", "empreintes du clip",
                         lambda: searching.fingerprint_clip(clip_path, s, info))

            def _candidates():  # noqa: ANN202
                with ctx.session_factory() as ss:
                    return find_candidates(ss, clip.frames, ctx.params, s.index_version, method)

            cands, stats = stage("candidates_ms", "recherche des candidats", _candidates)
            grid = frame_interval_ms(s.fingerprint_fps)
            decision = stage("alignment_ms", "alignement temporel", lambda: identify(
                clip.frames, cands, info.duration_ms, grid, clip.step_ms, ctx.params))
            payload = searching.decision_payload(decision, ctx.session_factory)
            timings["total_ms"] = round((time.perf_counter() - t_all) * 1000, 1)
            payload.update({
                "clip": {"name": clip_name, "duration_ms": info.duration_ms, "width": info.width,
                         "height": info.height, "fps": info.fps, "codec": info.codec,
                         "frames": len(clip.frames), "sample_step_ms": clip.step_ms},
                "candidate_stats": stats.to_dict(), "timings": timings, "method": method,
                "params": {"max_hamming": ctx.params.max_hamming, "accept_score": ctx.params.accept_score,
                           "min_coverage": ctx.params.min_coverage,
                           "ambiguity_ratio": ctx.params.ambiguity_ratio},
            })
            best = decision.best
            ctx.writer.run_sync(lambda ss: repo.save_search(
                ss, id=job.id, job_id=job.id, status=decision.status, clip_name=clip_name[:300], method=method,
                query_duration_ms=info.duration_ms, best_movie_id=best.movie_id if best else None,
                start_ms=best.start_ms if best else None, end_ms=best.end_ms if best else None,
                score=best.score if best else (decision.candidates[0].score if decision.candidates else None),
                elapsed_ms=int(timings["total_ms"]), result_json=json.dumps(payload)))
            with self._lock:
                job.details.update({"status": decision.status, "timings": timings})
            self._finish(job, "completed", stage="résultat disponible")
        except JobCancelled:
            self._search_failed(job, "cancelled", "recherche annulée")
        except (searching.QueryRejected, FFmpegError, ValueError) as exc:
            self._search_failed(job, "failed", str(exc))
        except Exception as exc:  
            log.exception("recherche en échec", extra={"job_id": job.id})
            self._search_failed(job, "failed", f"erreur inattendue : {exc}")
        finally:
            clip_path.unlink(missing_ok=True)

    def _search_failed(self, job: JobState, state: str, message: str) -> None:
        try:
            self.ctx.writer.run_sync(lambda ss: repo.save_search(
                ss, id=job.id, job_id=job.id, status="failed" if state == "failed" else "cancelled",
                clip_name=job.label[:300], result_json=json.dumps({"error": message})))
        except Exception:  
            log.exception("persistance de la recherche impossible", extra={"job_id": job.id})
        finally:
            self._finish(job, state, message)
