"""``DbWriter`` : **unique propriétaire des écritures SQLite** (thread dédié + file bornée).

Pourquoi ?
  SQLite n'accepte qu'un écrivain à la fois.  Plutôt que de laisser plusieurs threads se
  battre pour le verrou (``database is locked``), toutes les écritures passent par une
  ``queue.Queue`` bornée consommée par **un seul thread**.  C'est le schéma
  producteur–consommateur du cours (chapitre threads) :

    producteurs (threads des requêtes HTTP, threads des jobs, scripts)
          │  submit(fn)  ->  Future
          ▼
    queue.Queue(maxsize)  ──►  thread « sqlite-writer »  ──►  une transaction par fn

* ``queue.Queue`` est déjà thread-safe : **aucun Lock supplémentaire** n'est nécessaire.
* Chaque écriture s'exécute dans **une transaction** (``session.begin()``) : tout ou rien.
* Arrêt propre : ``close()`` refuse les nouvelles écritures (``threading.Event``), puis
  place une **sentinelle** en fin de file : comme la file est FIFO, toutes les écritures
  déjà soumises sont vidées avant l'arrêt, puis ``join()``.  Thread **non daemon** : on ne
  perd jamais une écriture importante à la fermeture.
* Les résultats remontent par ``concurrent.futures.Future`` : le thread producteur attend
  ``future.result()`` (``run_sync``) ou continue sans attendre (``submit``).
* L'ordre FIFO de la file est l'ordre d'écriture : un producteur qui doit garantir qu'un
  instantané plus récent n'est jamais écrasé par un plus ancien soumet l'écriture **sous
  son propre verrou** (voir ``JobManager._persist``).
"""

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
_STOP = object()


class WriterClosed(RuntimeError):
    pass


class WriterBusy(RuntimeError):
    pass


class DbWriter:
    def __init__(self, session_factory: sessionmaker[Session], maxsize: int = 1024):
        self._session_factory = session_factory
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=maxsize)
        self._closed = threading.Event()
        self._thread = threading.Thread(target=self._run, name="sqlite-writer", daemon=False)
        self.completed = 0  # modifié uniquement par le thread écrivain

    def start(self) -> None:
        self._thread.start()

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()

    def submit(self, fn: Callable[[Session], T], *, block: bool = False, timeout: float = 30.0) -> Future[T]:
        """Ajoute une écriture à la file.  ``block=False`` (défaut) ne bloque jamais l'appelant :
        si la file est pleine, ``WriterBusy`` est levée (l'API répond 503 au lieu de geler).
        Avec ``block=True``, l'appelant attend au plus ``timeout`` secondes qu'une place se libère."""
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
        """Soumet l'écriture puis attend son résultat (exécutée par le thread écrivain)."""
        return self.submit(fn, block=True).result(timeout=timeout)

    def _run(self) -> None:
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
                except BaseException as exc:  # l'erreur est transmise à l'appelant
                    log.debug("écriture échouée: %s", exc)
                    fut.set_exception(exc)
                self.completed += 1
            finally:
                self._queue.task_done()

    def close(self, timeout: float | None = 60.0) -> None:
        """Refuse les nouvelles écritures, vide la file puis arrête le thread."""
        if self._closed.is_set():
            return
        self._closed.set()
        if self._thread.is_alive():
            self._queue.put(_STOP)
            self._thread.join(timeout)
