"""E/S bloquantes locales : ``ThreadPoolExecutor`` borné + utilitaires fichiers.

Pourquoi des **threads** ici ?
  Copier un upload, calculer un SHA-256 en flux, lancer ffprobe ou générer une vignette
  sont des opérations qui passent l'essentiel de leur temps à **attendre** le disque ou un
  sous-processus.  Pendant ces attentes, CPython relâche le GIL : quelques threads suffisent
  et coûtent bien moins cher que des processus.  On les borne (``IO_THREADS=2``) pour ne pas
  saturer le disque : même si 40 requêtes HTTP arrivent en même temps (FastAPI exécute chaque
  requête dans un thread de son propre pool), au plus 2 copies / SHA-256 / vignettes touchent
  le disque simultanément ; les autres attendent leur tour dans la file de l'exécuteur.

Synchronisation minimale :
  * le ``ThreadPoolExecutor`` gère sa propre file thread-safe : aucun verrou ajouté ;
  * ``KeyedLocks`` : un vrai état partagé entre threads (le dictionnaire des verrous par
    clé) protégé par un ``threading.Lock`` ; il évite que deux requêtes simultanées génèrent
    la même vignette en même temps (sinon : deux FFmpeg écrivent le même fichier).
"""

from __future__ import annotations

import hashlib
import os
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO, TypeVar

T = TypeVar("T")
CHUNK = 1024 * 1024  # 1 Mo : mémoire bornée quelle que soit la taille du fichier


class IOExecutor:
    def __init__(self, threads: int):
        self.threads = threads
        self._executor = ThreadPoolExecutor(max_workers=threads, thread_name_prefix="io")

    def submit(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> Future[T]:
        return self._executor.submit(fn, *args, **kwargs)

    def call(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """Exécute ``fn`` dans un thread E/S borné et attend son résultat (ou son exception)."""
        return self._executor.submit(fn, *args, **kwargs).result()

    def shutdown(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=True)


class KeyedLocks:
    """Un verrou par clé (ex. une vignette), créé à la demande."""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._locks: dict[str, list] = {}  # clé -> [lock, nombre d'utilisateurs]

    @contextmanager
    def hold(self, key: str):
        with self._guard:  # section critique courte : seulement la mise à jour du dictionnaire
            entry = self._locks.setdefault(key, [threading.Lock(), 0])
            entry[1] += 1
        try:
            with entry[0]:
                yield
        finally:
            with self._guard:
                entry[1] -= 1
                if entry[1] == 0:
                    self._locks.pop(key, None)

    def __len__(self) -> int:
        with self._guard:
            return len(self._locks)


class UploadTooLarge(ValueError):
    pass


def _human_size(n: int) -> str:
    return f"{n / 1024**3:.1f} Go" if n >= 1024**3 else f"{n / 1024**2:.0f} Mo"


def copy_stream_with_hash(src: BinaryIO, dest: Path, max_bytes: int) -> tuple[int, str]:
    """Copie ``src`` vers ``dest`` par blocs de 1 Mo en calculant le SHA-256 au passage.

    Interrompt (et supprime ``dest``) si la taille dépasse ``max_bytes``.
    """
    digest = hashlib.sha256()
    size = 0
    try:
        with open(dest, "wb") as out:
            while True:
                chunk = src.read(CHUNK)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise UploadTooLarge(f"fichier trop volumineux (limite {_human_size(max_bytes)})")
                digest.update(chunk)
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    return size, digest.hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_child(base: Path, name: str) -> Path:
    """Résout ``base/name`` et vérifie (chemin canonique) qu'il reste DANS ``base``.

    Protège contre ``../``, les chemins absolus et les liens symboliques sortants.
    """
    if not name or name in (".", "..") or "/" in name or "\\" in name or ":" in name or "\x00" in name:
        raise ValueError("nom de fichier refusé")
    base_r = base.resolve()
    target = (base_r / name).resolve()
    if target.parent != base_r:
        raise ValueError("chemin hors du stockage de l'application")
    return target
