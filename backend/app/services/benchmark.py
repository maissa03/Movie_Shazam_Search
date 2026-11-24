from __future__ import annotations

import hashlib
import json
import os
import platform
import random
import statistics
import sys
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import psutil

from app.concurrency.process_pool import LocalProcessPool, wait_ready
from app.core.settings import Settings
from app.db import repo
from app.db.search_repo import band_candidates, exhaustive_candidates
from app.db.session import init_db, make_engine, make_session_factory
from app.db.writer import DbWriter
from app.services.indexing import build_task, plan_for
from app.video.ffmpeg_io import ffmpeg_version
from app.video.fingerprint import from_blob
from app.video.matching import MatchParams, QueryFrame
from app.video.pipeline import SegmentResult, analyze_segment

Publish = Callable[[dict], None]


@dataclass
class CorpusItem:
    path: Path
    duration_ms: int
    label: str
    content_hash: str = ""


def cpu_name() -> str:
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
                return str(winreg.QueryValueEx(k, "ProcessorNameString")[0]).strip()
        except OSError:
            pass
    if Path("/proc/cpuinfo").exists():
        for line in Path("/proc/cpuinfo").read_text(errors="ignore").splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    return platform.processor() or platform.machine()


def machine_info(settings: Settings) -> dict:
    vm = psutil.virtual_memory()
    return {
        "cpu": cpu_name(), "physical_cores": psutil.cpu_count(logical=False), "logical_cpus": psutil.cpu_count(),
        "ram_total_gb": round(vm.total / 1024**3, 1), "ram_available_gb": round(vm.available / 1024**3, 1),
        "os": f"{platform.system()} {platform.release()}", "python": platform.python_version(),
        "ffmpeg": ffmpeg_version(),
        "engine": {"index_version": settings.index_version, "fingerprint_fps": settings.fingerprint_fps,
                   "ffmpeg_threads_per_process": settings.ffmpeg_threads,
                   "segment_max_s": settings.segment_duration_seconds, "min_segment_s": settings.min_segment_seconds,
                   "target_min_segments": settings.target_min_segments},
    }


class ResourceSampler(threading.Thread):

    def __init__(self, interval: float = 0.2):
        super().__init__(name="resource-sampler", daemon=True)
        self.interval = interval
        self._stop_evt = threading.Event()
        self._lock = threading.Lock()
        self._peak_rss = 0
        self._cpu: list[float] = []

    def run(self) -> None:
        me = psutil.Process()
        psutil.cpu_percent(None)
        while not self._stop_evt.wait(self.interval):
            rss = 0
            for p in [me, *me.children(recursive=True)]:
                try:
                    rss += p.memory_info().rss
                except psutil.Error:
                    pass
            cpu = psutil.cpu_percent(None)
            with self._lock:
                self._peak_rss = max(self._peak_rss, rss)
                self._cpu.append(cpu)

    def snapshot(self) -> dict:
        with self._lock:
            cpu = statistics.fmean(self._cpu) if self._cpu else None
            return {"peak_ram_mb": round(self._peak_rss / 1024**2, 1),
                    "cpu_percent_avg": round(cpu, 1) if cpu is not None else None,
                    "busy_cores_avg": round(cpu / 100 * (psutil.cpu_count() or 1), 2) if cpu is not None else None}

    def stop(self) -> dict:
        self._stop_evt.set()
        self.join()
        return self.snapshot()


def dataset_id(items: list[CorpusItem], settings: Settings) -> str:
    h = hashlib.sha256()
    for it in sorted(items, key=lambda i: i.label):
        h.update(f"{it.label}|{it.content_hash or it.path.stat().st_size}|{it.duration_ms}".encode())
    h.update(settings.index_version.encode())
    return h.hexdigest()[:16]


def _digest(results: list[tuple[int, SegmentResult]]) -> str:
    h = hashlib.sha256()
    for item_no, r in sorted(results, key=lambda x: (x[0], x[1].start_ms)):
        for ts, hv in zip(r.timestamps, r.hashes):
            h.update(f"{item_no}:{ts}:{hv};".encode())
    return h.hexdigest()


def _warm_cache(items: list[CorpusItem]) -> None:
    for it in items:
        with open(it.path, "rb") as f:
            while f.read(8 * 1024 * 1024):
                pass


def _write_scratch(results: list[tuple[int, SegmentResult]], items: list[CorpusItem], settings: Settings,
                   scratch: Path) -> float:
    scratch.unlink(missing_ok=True)
    engine = make_engine(scratch)
    init_db(engine)
    writer = DbWriter(make_session_factory(engine))
    writer.start()
    try:
        version = settings.index_version
        seg_ids: dict[tuple[int, int], int] = {}
        for n, it in enumerate(items):
            seg_ms, plans = plan_for(it.duration_ms, settings)

            def setup(s, n=n, it=it, seg_ms=seg_ms, plans=plans):  # noqa: ANN001
                m = repo.create_movie(s, title=it.label, year=None, original_filename=it.label,
                                      stored_name=f"bench_{n}", file_size=0, file_mtime_ns=0,
                                      content_hash=f"bench{n}", duration_ms=it.duration_ms, width=0, height=0,
                                      codec="", fps=0, format_name="")
                todo = repo.prepare_index(s, m.id, version, seg_ms, plans)
                return m.id, {t.start_ms: t.segment_id for t in todo}

            mid, mapping = writer.run_sync(setup)
            for start, sid in mapping.items():
                seg_ids[(n, start)] = (mid, sid)
        t0 = time.perf_counter()
        futures = [writer.submit(lambda s, n=n, r=r: repo.save_segment_result(
            s, seg_ids[(n, r.start_ms)][0], seg_ids[(n, r.start_ms)][1], version, r), block=True)
            for n, r in results]
        for f in futures:
            f.result()
        return time.perf_counter() - t0
    finally:
        writer.close()
        engine.dispose()
        for suffix in ("", "-wal", "-shm"):
            Path(str(scratch) + suffix).unlink(missing_ok=True)


def run_index_benchmark(items: list[CorpusItem], settings: Settings, modes: list[int], repeats: int = 1,
                        publish: Publish | None = None, scratch_dir: Path | None = None) -> dict:
    publish = publish or (lambda _u: None)
    scratch_dir = scratch_dir or settings.temp_dir
    scratch_dir.mkdir(parents=True, exist_ok=True)
    modes = sorted(set(modes), key=lambda m: (m != 0, m))
    if 0 not in modes:
        modes = [0, *modes]
    tasks: list[tuple[int, object]] = []
    for n, it in enumerate(items):
        _, plans = plan_for(it.duration_ms, settings)
        tasks += [(n, build_task(it.path, p, settings)) for p in plans]
    total_steps = len(modes) * repeats
    publish({"stage": "préchauffage du cache disque", "completed_units": 0, "total_units": total_steps})
    _warm_cache(items)

    runs: list[dict] = []
    reference: str | None = None
    step = 0
    for r in range(repeats):
        order = modes[r % len(modes):] + modes[: r % len(modes)] if repeats > 1 else modes
        for mode in order:
            label = "séquentiel" if mode == 0 else f"{mode} processus"
            publish({"stage": f"répétition {r + 1}/{repeats} : {label}", "completed_units": step})
            pool = None
            startup_s = 0.0
            if mode > 0:
                t_s = time.perf_counter()
                pool = LocalProcessPool(mode)
                pids = pool.start(warmup=True)
                startup_s = time.perf_counter() - t_s
                publish({"pids": pids})
            sampler = ResourceSampler()
            sampler.start()
            t0 = time.perf_counter()
            results: list[tuple[int, SegmentResult]] = []
            try:
                if pool is None:
                    for n, task in tasks:
                        results.append((n, analyze_segment(task)))  # type: ignore[arg-type]
                else:
                    pending = {pool.apply_async(task): n for n, task in tasks}
                    while pending:
                        for work in wait_ready(pending):
                            results.append((pending.pop(work), work.get()))
                elapsed = time.perf_counter() - t0
            finally:
                res_usage = sampler.stop()
                if pool is not None:
                    pool.shutdown()
            write_s = _write_scratch(results, items, settings, scratch_dir / f"bench-{uuid.uuid4().hex[:8]}.db")
            digest = _digest(results)
            if mode == 0 and reference is None:
                reference = digest
            frames = sum(x.frames_decoded for _, x in results)
            fps_count = sum(x.count for _, x in results)
            runs.append({
                "repeat": r + 1, "mode": "sequential" if mode == 0 else "parallel", "process_count": max(mode, 1),
                "workers_label": label, "elapsed_ms": round(elapsed * 1000, 1), "write_ms": round(write_s * 1000, 1),
                "pool_startup_ms": round(startup_s * 1000, 1), "segments": len(results), "frames_decoded": frames,
                "fingerprints": fps_count, "frames_per_s": round(frames / elapsed, 1),
                "fingerprints_per_s": round(fps_count / elapsed, 1),
                "decode_s_sum": round(sum(x.decode_s for _, x in results), 3),
                "hash_s_sum": round(sum(x.hash_s for _, x in results), 3),
                "distinct_pids": sorted({x.pid for _, x in results}), "errors": 0,
                "identical_to_sequential": digest == reference, **res_usage,
            })
            step += 1
            publish({"completed_units": step})

    summary = []
    seq_ms = statistics.median(x["elapsed_ms"] for x in runs if x["mode"] == "sequential")
    for mode in modes:
        pc = max(mode, 1)
        sel = [x for x in runs if (x["mode"] == "sequential") == (mode == 0) and x["process_count"] == pc]
        med = statistics.median(x["elapsed_ms"] for x in sel)
        speedup = seq_ms / med if med else 0.0
        summary.append({
            "mode": sel[0]["mode"], "process_count": pc, "label": sel[0]["workers_label"],
            "elapsed_ms_median": round(med, 1), "elapsed_ms_all": [x["elapsed_ms"] for x in sel],
            "write_ms_median": round(statistics.median(x["write_ms"] for x in sel), 1),
            "speedup": round(speedup, 3), "efficiency": round(speedup / pc, 3),
            "frames_per_s": round(statistics.median(x["frames_per_s"] for x in sel), 1),
            "fingerprints_per_s": round(statistics.median(x["fingerprints_per_s"] for x in sel), 1),
            "peak_ram_mb": max(x["peak_ram_mb"] for x in sel),
            "cpu_percent_avg": statistics.fmean(x["cpu_percent_avg"] or 0 for x in sel),
            "busy_cores_avg": statistics.fmean(x["busy_cores_avg"] or 0 for x in sel),
            "distinct_pids": sel[-1]["distinct_pids"], "identical_to_sequential": all(x["identical_to_sequential"]
                                                                                     for x in sel),
            "pool_startup_ms": sel[-1]["pool_startup_ms"],
        })
    publish({"stage": "terminé", "completed_units": total_steps})
    return {"runs": runs, "summary": summary, "segments": len(tasks),
            "corpus": [{"label": it.label, "duration_ms": it.duration_ms} for it in items],
            "corpus_duration_ms": sum(it.duration_ms for it in items)}


def run_band_recall_benchmark(session_factory, settings: Settings, params: MatchParams,  # noqa: ANN001
                              samples: int = 150, distances: tuple[int, ...] = (0, 2, 4, 6, 8, 10, 12),
                              seed: int = 2026) -> dict:
    rng = random.Random(seed)
    with session_factory() as s:
        from sqlalchemy import select

        from app.db.models import Fingerprint, Movie
        rows = s.execute(select(Fingerprint.movie_id, Fingerprint.timestamp_ms, Fingerprint.hash_blob)
                         .join(Movie, Movie.id == Fingerprint.movie_id)
                         .where(Movie.status == "indexed", Fingerprint.algorithm_version == settings.index_version,
                                Fingerprint.informativeness >= params.min_informativeness)).all()
    if not rows:
        return {"available": False, "reason": "aucune empreinte indexée"}
    picked = rng.sample(rows, min(samples, len(rows)))
    out = []
    for radius in (0, 1):
        p = MatchParams(**{**params.to_dict(), "probe_radius": radius, "max_candidates_per_frame": 10_000})
        for d in distances:
            frames, truth = [], {}
            for i, (mid, ts, blob) in enumerate(picked):
                h = from_blob(blob)
                for b in rng.sample(range(64), d):
                    h ^= 1 << b
                frames.append(QueryFrame(i, i * 100, h, 100.0))
                truth[i] = (mid, ts)
            with session_factory() as s:
                t0 = time.perf_counter()
                cands, stats = band_candidates(s, frames, p, settings.index_version)
                band_ms = (time.perf_counter() - t0) * 1000
            found = {(c.query_index) for c in cands if (c.movie_id, c.movie_ts_ms) == truth[c.query_index]}
            out.append({"probe_radius": radius, "distance": d, "recall": round(len(found) / len(frames), 4),
                        "band_ms": round(band_ms, 1), "rows_scanned": stats.rows_scanned})
    with session_factory() as s:
        frames = [QueryFrame(i, i * 100, from_blob(b), 100.0) for i, (_, _, b) in enumerate(picked)]
        t0 = time.perf_counter()
        exhaustive_candidates(s, frames, params, settings.index_version)
        exh_ms = (time.perf_counter() - t0) * 1000
    return {"available": True, "samples": len(picked), "index_size": len(rows), "curve": out,
            "exhaustive_ms_same_queries": round(exh_ms, 1), "max_hamming": params.max_hamming}


def summarize_for_db(run_id: str, ds: str, result: dict, machine: dict) -> list[dict]:
    rows = []
    for x in result["summary"]:
        rows.append({"run_id": run_id, "kind": "index", "mode": x["mode"], "process_count": x["process_count"],
                     "dataset_id": ds, "elapsed_ms": x["elapsed_ms_median"],
                     "frames_count": int(x["frames_per_s"] * x["elapsed_ms_median"] / 1000),
                     "peak_ram_mb": x["peak_ram_mb"],
                     "metrics_json": json.dumps({**x, "machine": machine, "corpus": result["corpus"],
                                                 "segments": result["segments"]})})
    return rows


def default_modes() -> list[int]:
    cpus = os.cpu_count() or 1
    return [0, 1, 2] + ([4] if cpus >= 4 else [])
