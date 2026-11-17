"""Pool de processus local (``concurrent.futures.ProcessPoolExecutor`` adossé à ``multiprocessing``).

Pourquoi des **processus** pour l'indexation ?
  Chaque segment = décodage FFmpeg + mise à l'échelle + pHash : c'est du calcul (CPU-bound).
  En CPython, le GIL empêche plusieurs threads d'exécuter du bytecode Python en même temps ;
  des processus ont chacun leur interpréteur et leur mémoire : ils s'exécutent réellement
  sur plusieurs cœurs.  Chaque processus du pool pilote aussi son propre FFmpeg.

Choix de ``ProcessPoolExecutor`` plutôt que ``multiprocessing.Pool`` :
  même mécanisme (processus + files de messages), mais il renvoie des
  ``concurrent.futures.Future`` : le thread d'indexation attend « le premier segment terminé »
  avec ``concurrent.futures.wait(FIRST_COMPLETED)`` et peut annuler individuellement les
  segments pas encore démarrés (``Future.cancel``).  Une seule solution est utilisée.

Règles respectées :
  * contexte ``spawn`` explicite sur toutes les plateformes (défaut Windows ; évite les
    pièges de ``fork`` avec des threads sur Linux) ;
  * pool **créé une fois** (cycle de vie FastAPI) et réutilisé : une tâche = un segment ;
  * fonctions de module + arguments sérialisables (``SegmentTask``) ;
  * arrêt : ``Event`` d'abandon -> les segments en cours s'interrompent à la prochaine image,
    FFmpeg est tué, puis ``shutdown(cancel_futures=True)``.
"""

from __future__ import annotations

import logging
import multiprocessing as mp
from collections.abc import Callable
from concurrent.futures import Future, ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from typing import Any

from app.video.pipeline import worker_initializer, worker_ping

log = logging.getLogger("movieshazam.pool")


class LocalProcessPool:
    def __init__(self, workers: int):
        if workers < 1:
            raise ValueError("au moins un processus")
        self.workers = workers
        self._ctx = mp.get_context("spawn")
        self._abort = self._ctx.Event()
        self._executor: ProcessPoolExecutor | None = None
        self.pids: list[int] = []
        self.restarts = 0

    def _create(self) -> ProcessPoolExecutor:
        barrier = self._ctx.Barrier(self.workers)
        self._barrier = barrier
        return ProcessPoolExecutor(max_workers=self.workers, mp_context=self._ctx,
                                   initializer=worker_initializer, initargs=(self._abort, barrier))

    def start(self, warmup: bool = True) -> list[int]:
        """Crée le pool ; le préchauffage démarre les N processus et relève leurs PID."""
        self._executor = self._create()
        if warmup:
            futures = [self._executor.submit(worker_ping, True) for _ in range(self.workers)]
            self.pids = sorted({f.result(timeout=180) for f in futures})
            log.info("pool de %d processus prêt, PID=%s", self.workers, self.pids)
        return self.pids

    @property
    def started(self) -> bool:
        return self._executor is not None

    def submit(self, fn: Callable[..., Any], *args: Any) -> Future:
        if self._executor is None:
            raise RuntimeError("pool non démarré")
        try:
            return self._executor.submit(fn, *args)
        except BrokenProcessPool:
            # un processus a été tué (ex. manque de mémoire) : on recrée le pool une fois
            log.error("pool cassé : recréation")
            self.restarts += 1
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = self._create()
            return self._executor.submit(fn, *args)

    def shutdown(self, wait: bool = True) -> None:
        if self._executor is None:
            return
        self._abort.set()  # les segments en cours s'arrêtent à la prochaine image
        self._executor.shutdown(wait=wait, cancel_futures=True)
        self._executor = None
        log.info("pool arrêté")

    def __enter__(self) -> LocalProcessPool:
        if not self.started:
            self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.shutdown()
