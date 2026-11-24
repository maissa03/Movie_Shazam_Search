from __future__ import annotations

import logging
import multiprocessing as mp
import threading
from collections.abc import Collection
from multiprocessing.pool import ApplyResult, Pool
from weakref import WeakSet

from app.video.pipeline import SegmentResult, SegmentTask, analyze_segment, worker_initializer, worker_ping

log = logging.getLogger("movieshazam.pool")


class PoolRun:
    def __init__(self, pool: Pool):
        self.pool = pool
        # Pool replaces dead workers but can leave their AsyncResults unresolved.
        self.processes = tuple(pool._pool)
        self.results: WeakSet[ApplyResult] = WeakSet()

    @property
    def broken(self) -> bool:
        return any(process.exitcode is not None for process in self.processes)


class SegmentWork:
    def __init__(self, run: PoolRun, result: ApplyResult):
        self._run = run
        self._result = result

    def ready(self) -> bool:
        return self._result.ready() or self._run.broken

    def get(self) -> SegmentResult:
        if self._result.ready():
            return self._result.get()
        raise RuntimeError("un processus du pool s’est arrêté pendant le calcul")


def wait_ready(pending: Collection[SegmentWork]) -> list[SegmentWork]:
    while pending:
        done = [work for work in pending if work.ready()]
        if done:
            return done
        next(iter(pending))._result.wait(0.02)
    return []


class LocalProcessPool:
    def __init__(self, workers: int):
        if workers < 1:
            raise ValueError("au moins un processus")
        self.workers = workers
        self._lock = threading.Lock()
        self._run: PoolRun | None = None
        self.pids: list[int] = []

    def _create(self) -> PoolRun:
        # Sous Windows, multiprocessing utilise « spawn » : chaque worker est un nouvel interpréteur Python
        # qui importe app.video.pipeline puis exécute worker_initializer(abort, barrier).
        self._abort = mp.Event()
        barrier = mp.Barrier(self.workers)
        pool = mp.Pool(self.workers, initializer=worker_initializer, initargs=(self._abort, barrier))
        run = PoolRun(pool)
        self.pids = sorted(p.pid for p in run.processes)
        return run

    def start(self) -> list[int]:
        with self._lock:
            if self._run is None:
                run = self._create()
                try:
                    # Préchauffage : une tâche worker_ping par worker. La Barrier oblige chaque worker à
                    # attendre les autres, donc les N pings sont servis par N processus distincts.
                    self.pids = sorted(set(run.pool.map_async(worker_ping, [True] * self.workers,
                                                              chunksize=1).get(timeout=180)))
                except BaseException:
                    run.pool.terminate()
                    run.pool.join()
                    raise
                self._run = run
                log.info("[POOL] pool démarré : %d processus workers, PID %s", self.workers,
                         ", ".join(map(str, self.pids)))
            return self.pids

    def apply_async(self, task: SegmentTask) -> SegmentWork:
        with self._lock:
            run = self._run
            if run is None:
                raise RuntimeError("pool non démarré")
            if run.broken:
                log.error("pool cassé : recréation")
                self._abort.set()
                run.pool.terminate()
                run.pool.join()
                self._run = run = self._create()
            result = run.pool.apply_async(analyze_segment, (task,))
            run.results.add(result)
            return SegmentWork(run, result)

    def shutdown(self) -> None:
        with self._lock:
            run = self._run
            if run is None:
                return
            self._run = None
            self._abort.set()
            # Drain accepted work before close so worker exits still indicate a lost result.
            for result in list(run.results):
                while not result.ready() and not run.broken:
                    result.wait(0.05)
            if run.broken:
                run.pool.terminate()
            else:
                run.pool.close()
            run.pool.join()
            log.info("pool arrêté")
