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
CHUNK = 1024 * 1024


class IOExecutor:
    def __init__(self, threads: int):
        self.threads = threads
        self._executor = ThreadPoolExecutor(max_workers=threads, thread_name_prefix="io")

    def submit(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> Future[T]:
        return self._executor.submit(fn, *args, **kwargs)

    def call(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        return self._executor.submit(fn, *args, **kwargs).result()

    def shutdown(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=True)


class KeyedLocks:

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._locks: dict[str, list] = {}

    @contextmanager
    def hold(self, key: str):
        with self._guard:
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
    if not name or name in (".", "..") or "/" in name or "\\" in name or ":" in name or "\x00" in name:
        raise ValueError("nom de fichier refusé")
    base_r = base.resolve()
    target = (base_r / name).resolve()
    if target.parent != base_r:
        raise ValueError("chemin hors du stockage de l'application")
    return target
