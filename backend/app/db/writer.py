from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from concurrent.futures import Future
from typing import Any, TypeVar

from sqlalchemy.orm import Session, sessionmaker

T = TypeVar("T")
log = logging.getLogger("movieshazam.db.writer")
_STOP = object()  # sentinelle : déposée dans la file par close() pour terminer run()


class WriterClosed(RuntimeError):
    pass


class WriterBusy(RuntimeError):
    pass


class DbWriter(threading.Thread):
    """Thread unique d'écriture SQLite (consommateur).

    Les autres threads (requêtes HTTP, coordinateur d'indexation, recherche) sont les producteurs : ils
    déposent une fonction d'écriture dans la file avec submit()/run_sync() et attendent son Future.
    run() exécute chaque écriture dans sa propre transaction, l'une après l'autre.
    """

    def __init__(self, session_factory: sessionmaker[Session], maxsize: int = 1024):
        super().__init__(name="sqlite-writer", daemon=False)
        self._session_factory = session_factory
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=maxsize)
        self._closed = threading.Event()
        self.completed = 0

    @property
    def alive(self) -> bool:
        return self.is_alive()

    def submit(self, fn: Callable[[Session], T], *, block: bool = False, timeout: float = 30.0) -> Future[T]:
        if self._closed.is_set():
            raise WriterClosed("la base est en cours de fermeture")
        fut: Future[T] = Future()
        try:
            if block:
                self._queue.put((fn, fut), timeout=timeout)
            else:
                self._queue.put_nowait((fn, fut))
        except queue.Full as exc:
            raise WriterBusy("file d'écriture SQLite pleine, réessayez") from exc
        return fut

    def run_sync(self, fn: Callable[[Session], T], timeout: float | None = 120.0) -> T:
        return self.submit(fn, block=True).result(timeout=timeout)

    def run(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is _STOP:
                    return
                fn, fut = item
                if not fut.set_running_or_notify_cancel():
                    continue
                try:
                    with self._session_factory() as session, session.begin():
                        result = fn(session)
                    fut.set_result(result)
                except BaseException as exc:
                    log.debug("écriture échouée: %s", exc)
                    fut.set_exception(exc)
                self.completed += 1
            finally:
                self._queue.task_done()

    def close(self, timeout: float | None = 60.0) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        if self.is_alive():
            self._queue.put(_STOP)
            self.join(timeout)
