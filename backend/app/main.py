"""Application FastAPI : démarrage, arrêt et configuration — **sans asyncio**.

Le code de l'application est entièrement synchrone :
  * les routes sont des fonctions ``def`` que FastAPI exécute dans les threads de son pool ;
  * le démarrage et l'arrêt sont des fonctions ordinaires enregistrées comme gestionnaires
    ``on_startup`` / ``on_shutdown`` (FastAPI les appelle une fois, au lancement et à l'arrêt) ;
  * les traitements de fond sont des threads (``JobManager``) et des processus (pool).
(Uvicorn, le serveur HTTP, utilise en interne sa propre boucle d'événements pour lire les
sockets ; l'application ne la manipule jamais.)

Démarrage :  SQLite (WAL) -> thread écrivain -> nettoyage des jobs interrompus et des fichiers
             temporaires -> vérification de la bibliothèque -> pool de processus (préchauffé,
             PID relevés) -> pool de threads E/S -> gestionnaire de jobs (threads).
Arrêt :      refus des nouveaux jobs + annulation coopérative -> pool arrêté (segments en cours
             interrompus, FFmpeg tués) -> derniers threads de jobs -> threads E/S -> écritures
             vidées puis thread écrivain arrêté -> base fermée.

Lancer **un seul** processus Uvicorn (pas d'option ``--workers``) : plusieurs workers web
dupliqueraient les pools de processus, la mémoire et l'écrivain SQLite.  Le parallélisme CPU
vient du pool interne, pas du nombre de réplicas du serveur.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker
from starlette.middleware.body_limit import RequestBodyLimitMiddleware

from app.api.routes import register_upload_routes, router
from app.concurrency.job_manager import JobManager
from app.concurrency.process_pool import LocalProcessPool
from app.concurrency.thread_io import IOExecutor
from app.core.logging import setup_logging
from app.core.settings import Settings, get_settings
from app.db import repo
from app.db.session import init_db, make_engine, make_session_factory
from app.db.writer import DbWriter, WriterBusy, WriterClosed
from app.services import library
from app.video.ffmpeg_io import FFmpegNotFound, find_binary
from app.video.matching import MatchParams

T = TypeVar("T")
log = logging.getLogger("movieshazam.app")

DEFAULT_BODY_LIMIT = 10 * 1024**2  # requêtes JSON ordinaires
MULTIPART_SLACK = 1024**2  # en-têtes multipart autour du fichier envoyé


@dataclass
class AppContext:
    settings: Settings
    engine: Engine
    session_factory: sessionmaker[Session]
    writer: DbWriter
    pool: LocalProcessPool
    io: IOExecutor
    params: MatchParams
    jobs: JobManager = None  # type: ignore[assignment]

    def read(self, fn: Callable[[Session], T]) -> T:
        """Lecture SQLite courte, dans le thread appelant, avec une session dédiée (jamais partagée)."""
        with self.session_factory() as s:
            return fn(s)


def _clean_temp(settings: Settings) -> int:
    n = 0
    for p in settings.temp_dir.glob("*.part"):
        p.unlink(missing_ok=True)
        n += 1
    return n


def startup(settings: Settings) -> AppContext:
    """Démarre tous les composants, dans l'ordre.  En cas d'échec, arrête ceux déjà lancés
    (sinon un thread non daemon, comme l'écrivain, empêcherait le processus de se terminer)."""
    setup_logging()
    settings.ensure_dirs()
    try:
        find_binary("ffmpeg")
        find_binary("ffprobe")
    except FFmpegNotFound as exc:
        log.error("%s", exc)
    engine = make_engine(settings.database_path)
    init_db(engine)
    sf = make_session_factory(engine)
    writer = DbWriter(sf)
    writer.start()
    pool: LocalProcessPool | None = None
    io: IOExecutor | None = None
    try:
        interrupted = writer.run_sync(repo.fail_interrupted_jobs)
        removed = _clean_temp(settings)
        report = library.verify_library(sf, writer, settings)
        log.info("démarrage : %d job(s) interrompu(s), %d fichier(s) temporaire(s) supprimé(s), bibliothèque %s",
                 interrupted, removed, report)
        pool = LocalProcessPool(settings.effective_workers())
        pool.start()
        io = IOExecutor(settings.io_threads)
        ctx = AppContext(settings, engine, sf, writer, pool, io, MatchParams())
        ctx.jobs = JobManager(ctx)
        ctx.jobs.start()
    except BaseException:
        if pool is not None:
            pool.shutdown()
        if io is not None:
            io.shutdown()
        writer.close()
        engine.dispose()
        raise
    log.info("MovieShazam prêt sur http://%s:%d (%d processus, PID %s)", settings.app_host, settings.app_port,
             pool.workers, pool.pids)
    return ctx


def shutdown(ctx: AppContext) -> None:
    log.info("arrêt en cours...")
    ctx.jobs.stop()  # refuse, annule, laisse finir les segments engagés (borné)
    ctx.pool.shutdown()  # Event d'abandon : les segments encore en cours s'interrompent
    ctx.jobs.join()  # derniers threads de jobs (ils constatent l'annulation)
    ctx.io.shutdown()
    ctx.writer.close()  # vide la file d'écritures puis arrête le thread écrivain
    ctx.engine.dispose()
    log.info("arrêt terminé")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app: FastAPI

    def on_startup() -> None:
        app.state.ctx = startup(settings)

    def on_shutdown() -> None:
        ctx = getattr(app.state, "ctx", None)
        if ctx is not None:
            shutdown(ctx)
            app.state.ctx = None

    app = FastAPI(
        title="MovieShazam (local)",
        version="1.0.0",
        description="Identification locale d'un extrait vidéo (pHash + alignement temporel). "
                    "Tout s'exécute sur ce PC : aucune ressource distante.",
        on_startup=[on_startup],
        on_shutdown=[on_shutdown],
    )
    # ajoutée en premier = la plus interne ; CORS (ajouté ensuite) reste la plus externe et
    # ajoute donc ses en-têtes même aux réponses 413
    app.add_middleware(RequestBodyLimitMiddleware, max_body_size=DEFAULT_BODY_LIMIT)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.frontend_origins,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["*"],
    )

    @app.exception_handler(WriterBusy)
    def _busy(_r: Request, exc: WriterBusy) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=503)

    @app.exception_handler(WriterClosed)
    def _closed(_r: Request, exc: WriterClosed) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=503)

    # routes de téléversement d'abord (limites propres), puis toutes les autres routes
    register_upload_routes(app, movie_limit=settings.max_upload_bytes + MULTIPART_SLACK,
                           clip_limit=settings.max_clip_bytes + MULTIPART_SLACK)
    app.include_router(router)
    return app


app = create_app()
