"""(8) annulation, erreurs, nettoyage, limites ; (9) condition de course ; (10) séquentiel == parallèle."""

from __future__ import annotations

import os
import threading
import time

import psutil
import pytest
from conftest import import_file, needs_ffmpeg
from sqlalchemy import text

from app.concurrency.process_pool import LocalProcessPool
from app.concurrency.thread_io import KeyedLocks
from app.db import repo
from app.db.writer import DbWriter, WriterBusy, WriterClosed
from app.services import indexing, library
from app.video.ffmpeg_io import FFmpegError, iter_gray_frames


# ------------------------------------------------------------------ (9) course
def test_race_condition_without_lock_loses_updates():
    """Démonstration : lecture-modification-écriture non protégée -> mises à jour perdues."""
    state = {"value": 0}
    n_threads, n_iter = 8, 2000
    barrier = threading.Barrier(n_threads)

    def unsafe() -> None:
        barrier.wait()  # tous les threads démarrent en même temps (contention maximale)
        for _ in range(n_iter):
            v = state["value"]
            time.sleep(0)  # cède le GIL entre la lecture et l'écriture : fenêtre de course
            state["value"] = v + 1

    threads = [threading.Thread(target=unsafe) for _ in range(n_threads)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert state["value"] < n_threads * n_iter  # des incréments ont été perdus


def test_lock_preserves_consistency():
    state = {"value": 0}
    lock = threading.Lock()
    n_threads, n_iter = 8, 2000
    barrier = threading.Barrier(n_threads)

    def safe() -> None:
        barrier.wait()
        for _ in range(n_iter):
            with lock:  # section critique courte
                v = state["value"]
                time.sleep(0)
                state["value"] = v + 1

    threads = [threading.Thread(target=safe) for _ in range(n_threads)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert state["value"] == n_threads * n_iter


def test_single_writer_serializes_concurrent_sqlite_updates(db):
    """Solution retenue dans MovieShazam : 8 threads soumettent 50 lectures-modifications-écritures
    SQLite chacun ; le thread écrivain unique les exécute une par une -> aucune perte."""
    sf, writer = db
    writer.run_sync(lambda s: s.execute(text("CREATE TABLE counter (v INTEGER)")))
    writer.run_sync(lambda s: s.execute(text("INSERT INTO counter VALUES (0)")))

    def increment(s):  # noqa: ANN001, ANN202
        v = s.execute(text("SELECT v FROM counter")).scalar()
        s.execute(text("UPDATE counter SET v = :v"), {"v": v + 1})

    barrier = threading.Barrier(8)

    def producer() -> None:
        barrier.wait()
        futures = [writer.submit(increment, block=True) for _ in range(50)]
        for f in futures:
            f.result()

    threads = [threading.Thread(target=producer) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    with sf() as s:
        assert s.execute(text("SELECT v FROM counter")).scalar() == 400


def test_writer_close_drains_queue_and_refuses_new_work(db):
    sf, _ = db
    writer = DbWriter(sf)
    writer.start()
    writer.run_sync(lambda s: s.execute(text("CREATE TABLE t (x INTEGER)")))
    futures = [writer.submit(lambda s, i=i: s.execute(text("INSERT INTO t VALUES (:x)"), {"x": i}), block=True)
               for i in range(200)]
    writer.close()  # sentinelle en fin de file : tout ce qui précède est écrit
    assert all(f.done() and f.exception() is None for f in futures)
    with sf() as s:
        assert s.execute(text("SELECT COUNT(*) FROM t")).scalar() == 200
    with pytest.raises(WriterClosed):
        writer.submit(lambda s: None)


def test_writer_queue_is_bounded(db):
    sf, _ = db
    writer = DbWriter(sf, maxsize=2)  # non démarré : la file se remplit
    writer.submit(lambda s: None)
    writer.submit(lambda s: None)
    with pytest.raises(WriterBusy):  # jamais de blocage de l'appelant (thread de requête -> 503)
        writer.submit(lambda s: None)


def test_keyed_locks_serialize_same_key_only():
    locks = KeyedLocks()
    active: dict[str, int] = {"a": 0, "b": 0}
    peak: dict[str, int] = {"a": 0, "b": 0}
    guard = threading.Lock()

    def work(key: str) -> None:
        with locks.hold(key):
            with guard:
                active[key] += 1
                peak[key] = max(peak[key], active[key])
            time.sleep(0.01)
            with guard:
                active[key] -= 1

    threads = [threading.Thread(target=work, args=(k,)) for k in "ab" * 6]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert peak == {"a": 1, "b": 1}
    assert len(locks) == 0  # verrous libérés et nettoyés


# ------------------------------------------------------------------ (8) FFmpeg / ressources
def _ffmpeg_children() -> list[psutil.Process]:
    return [p for p in psutil.Process().children(recursive=True) if "ffmpeg" in p.name().lower()]


@needs_ffmpeg
def test_ffmpeg_killed_when_consumer_stops_early(mini_corpus):
    corpus, gt = mini_corpus
    gen = iter_gray_frames(corpus / gt["movies"][0]["file"], start_ms=0, duration_ms=None, fps=2.0)
    next(gen)
    assert _ffmpeg_children()  # FFmpeg tourne
    gen.close()  # arrêt anticipé (annulation)
    time.sleep(0.2)
    assert not _ffmpeg_children()  # aucun FFmpeg orphelin


@needs_ffmpeg
def test_ffmpeg_timeout_raises_and_cleans_up(demo_corpus):
    corpus, gt = demo_corpus
    with pytest.raises(FFmpegError, match="délai"):
        for _ in iter_gray_frames(corpus / gt["movies"][0]["file"], start_ms=0, duration_ms=None, fps=2.0,
                                  timeout_s=0.3):
            pass
    assert not _ffmpeg_children()


@needs_ffmpeg
def test_truncated_file_fails_explicitly(mini_corpus, settings, db, tmp_path):
    """Fichier lisible par ffprobe mais tronqué : erreur de segment explicite, film « failed »."""
    corpus, gt = mini_corpus
    src = (corpus / gt["movies"][0]["file"]).read_bytes()
    broken = tmp_path / "tronque.mp4"
    broken.write_bytes(src[: int(len(src) * 0.35)])
    sf, writer = db
    movie = import_file(broken, settings, writer)
    rep = indexing.index_movie_sync(movie.id, library.movie_path(movie, settings), movie.duration_ms, settings,
                                    writer)
    assert not rep.ok
    with sf() as s:
        m = repo.get_movie(s, movie.id)
        assert m.status == "failed" and m.error
        assert any(x.status == "failed" for x in repo.movie_segments(s, movie.id))


# ------------------------------------------------------------------ (10) séquentiel == parallèle
@needs_ffmpeg
def test_sequential_and_parallel_produce_identical_index(mini_corpus, tmp_path):
    from conftest import make_settings

    from app.db.session import init_db, make_engine, make_session_factory

    corpus, gt = mini_corpus
    results = {}
    pids = {}
    for mode in ("seq", "par"):
        st = make_settings(tmp_path / mode)
        engine = make_engine(st.database_path)
        init_db(engine)
        sf = make_session_factory(engine)
        writer = DbWriter(sf)
        writer.start()
        pool = LocalProcessPool(2) if mode == "par" else None
        if pool:
            pool.start()
        try:
            out = {}
            for m in [m for m in gt["movies"] if m["indexed"]]:
                movie = import_file(corpus / m["file"], st, writer)
                rep = indexing.index_movie_sync(movie.id, library.movie_path(movie, st), movie.duration_ms, st,
                                                writer, pool)
                assert rep.ok, rep.message
                pids.setdefault(mode, set()).update(rep.pids)
                with sf() as s:
                    out[m["key"]] = repo.movie_fingerprints(s, movie.id, st.index_version)
            results[mode] = out
        finally:
            if pool:
                pool.shutdown()
            writer.close()
            engine.dispose()
    assert results["seq"] == results["par"]  # mêmes horodatages, mêmes hashes, même informativité
    assert pids["seq"] == {os.getpid()}  # séquentiel : le processus courant
    assert len(pids["par"]) == 2 and os.getpid() not in pids["par"]  # 2 processus locaux distincts


@needs_ffmpeg
def test_pool_warmup_proves_distinct_processes():
    with LocalProcessPool(2) as pool:
        assert len(pool.pids) == 2 and os.getpid() not in pool.pids


def test_application_code_does_not_use_asyncio():
    """Choix d'architecture : threads + processus uniquement, aucune coroutine dans l'application."""
    import re
    from pathlib import Path

    app_dir = Path(__file__).resolve().parents[1] / "app"
    pattern = re.compile(r"^\s*(import asyncio|from asyncio|async def |async with |async for )|\bawait\s", re.M)
    offenders = [str(p.relative_to(app_dir)) for p in app_dir.rglob("*.py")
                 if pattern.search(p.read_text(encoding="utf-8"))]
    assert not offenders, offenders
