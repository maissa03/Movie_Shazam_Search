"""Routes REST locales (FastAPI), toutes **synchrones** (``def``), sans asyncio ni coroutine.

FastAPI exécute chaque fonction ``def`` dans un **thread** de son pool de travail : une requête
lente (copie d'un upload, attente d'une écriture) n'empêche jamais les autres d'être servies.

* lectures SQLite courtes -> ``ctx.read`` (session dédiée au thread de la requête) ;
* écritures -> ``ctx.writer`` (thread écrivain unique) ;
* fichiers / ffprobe / vignettes -> ``ctx.io.call`` (pool de threads E/S borné à ``IO_THREADS``) ;
* indexation et recherche -> ``JobManager`` (threads d'arrière-plan) : réponse immédiate
  ``202 Accepted`` + identifiant de job, la progression se consulte par *polling* ``GET /api/jobs/{id}``.
"""

from __future__ import annotations

import csv
import io
import json
import platform
import shutil
import sys
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.routing import APIRoute
from sqlalchemy import text
from starlette.middleware.body_limit import RequestBodyLimitMiddleware

from app.api.schemas import (
    BenchmarkRunIn,
    DeleteOut,
    HealthOut,
    ImportOut,
    JobOut,
    MovieDetailOut,
    MovieOut,
    MovieUpdateIn,
    ReindexOut,
    SearchOut,
    SearchStartOut,
    SegmentOut,
)
from app.concurrency.job_manager import NotAccepting, QueueFull, job_row_to_dict
from app.concurrency.thread_io import UploadTooLarge, safe_child
from app.db import repo
from app.db.writer import WriterBusy, WriterClosed
from app.services import benchmark as bench
from app.services import library
from app.video.ffmpeg_io import FFmpegError, FFmpegNotFound, ffmpeg_version, find_binary

router = APIRouter(prefix="/api")


def register_upload_routes(app: FastAPI, movie_limit: int, clip_limit: int) -> None:
    """Enregistre les deux routes de téléversement **sur l'application** (pas dans ``router``),
    chacune avec sa propre limite de taille.

    ``RequestBodyLimitMiddleware`` (Starlette) répond 413 dès que l'en-tête ``Content-Length``
    dépasse la limite — avant de lire le corps — et compte aussi les octets réellement reçus.
    Une limite propre à une route n'est appliquée par FastAPI que pour une route ajoutée
    directement à l'application (les routes d'un routeur inclus ignorent ce réglage) ; ces routes
    sont donc créées pour chaque application, sans modifier d'état partagé entre applications.
    """
    specs = (
        ("/api/movies", import_movie, ImportOut, "Importer une vidéo et l'indexer", movie_limit),
        ("/api/search", start_search, SearchStartOut, "Identifier un extrait vidéo", clip_limit),
    )
    for path, endpoint, model, summary, limit in specs:
        app.add_api_route(path, endpoint, methods=["POST"], response_model=model, status_code=202, summary=summary)
        route = next(r for r in app.router.routes
                     if isinstance(r, APIRoute) and r.path == path and r.endpoint is endpoint)
        route.app = RequestBodyLimitMiddleware(route.app, max_body_size=limit)


def _ctx(request: Request):  # noqa: ANN202
    return request.app.state.ctx


def _job_out(ctx, job_id: str | None) -> JobOut | None:  # noqa: ANN001
    if not job_id:
        return None
    snap = ctx.jobs.snapshot(job_id)  # copie prise sous le verrou du gestionnaire de jobs
    return JobOut(**snap) if snap else None


# ----------------------------------------------------------------------- santé
@router.get("/health", response_model=HealthOut, summary="État du backend, de SQLite et de FFmpeg")
def health(request: Request) -> HealthOut:
    ctx = _ctx(request)
    s = ctx.settings
    db_ok, db_err = True, None
    try:
        ctx.read(lambda ss: ss.execute(text("SELECT 1")).scalar())
    except Exception as exc:  # noqa: BLE001
        db_ok, db_err = False, str(exc)
    try:
        ff = {"available": True, "ffmpeg": find_binary("ffmpeg"), "ffprobe": find_binary("ffprobe"),
              "version": ctx.io.call(ffmpeg_version)}
    except FFmpegNotFound as exc:
        ff = {"available": False, "error": str(exc)}
    usage = shutil.disk_usage(s.media_dir)
    return HealthOut(
        status="ok" if db_ok and ff["available"] and ctx.writer.alive else "degraded",
        app="MovieShazam", index_version=s.index_version,
        database={"ok": db_ok, "error": db_err, "journal_mode": "WAL", "writer_alive": ctx.writer.alive,
                  "path": "data/" + s.database_path.name},
        ffmpeg=ff,
        concurrency={"process_workers": ctx.pool.workers, "worker_pids": ctx.pool.pids, "io_threads": s.io_threads,
                     "max_active_index_jobs": s.max_active_index_jobs,
                     "max_active_search_jobs": s.max_active_search_jobs, "cpu_count": __import__("os").cpu_count(),
                     "accepting_jobs": ctx.jobs.accepting},
        limits={"max_upload_gb": s.max_upload_gb, "max_clip_mb": s.max_clip_upload_mb,
                "query_duration_s": [s.min_query_duration_seconds, s.max_query_duration_seconds],
                "disk_free_gb": round(usage.free / 1024**3, 1)},
        python=sys.version.split()[0], platform=platform.platform(),
    )


@router.get("/stats", summary="Chiffres du tableau de bord (vraies données)")
def stats(request: Request) -> dict:
    ctx = _ctx(request)

    def _read(s):  # noqa: ANN001, ANN202
        data = repo.library_stats(s)
        data["recent_searches"] = [
            {"id": x.id, "status": x.status, "clip_name": x.clip_name, "score": x.score, "start_ms": x.start_ms,
             "best_movie_id": x.best_movie_id,
             "best_title": (m.title if x.best_movie_id and (m := repo.get_movie(s, x.best_movie_id)) else None),
             "created_at": x.created_at.isoformat() + "Z"} for x in repo.list_searches(s, 6)]
        return data

    data = ctx.read(_read)
    data["active_jobs"] = ctx.jobs.snapshots(active_only=True)
    data["process_workers"] = ctx.pool.workers
    return data


# ----------------------------------------------------------------------- films
@router.get("/movies", response_model=list[MovieOut], summary="Liste des films (recherche, tri, filtre)")
def list_movies(request: Request, q: str | None = None,
                      sort: Literal["created_at", "title", "year", "duration", "size", "status"] = "created_at",
                      order: Literal["asc", "desc"] = "desc", status: str | None = None) -> list[MovieOut]:
    ctx = _ctx(request)
    movies = ctx.read(lambda s: repo.list_movies(s, q, sort, order, status))
    return [MovieOut.from_model(m, ctx.jobs.active_job_id(m.id)) for m in movies]


# POST /api/movies : enregistrée par register_upload_routes (limite de taille propre)
def import_movie(request: Request, response: Response,
                       file: Annotated[UploadFile, File(description="fichier vidéo")],
                       title: Annotated[str | None, Form()] = None,
                       year: Annotated[int | None, Form(ge=1870, le=2100)] = None) -> ImportOut:
    ctx = _ctx(request)
    s = ctx.settings
    if not ctx.jobs.accepting:
        raise HTTPException(503, "l'application s'arrête")
    try:
        library.safe_extension(file.filename or "")
        ctx.io.call(library.check_disk_space, s.temp_dir, file.size or 0)
        staged = ctx.io.call(library.stage_stream, file.file, file.filename or "video", s, s.max_upload_bytes)
    except UploadTooLarge as exc:
        raise HTTPException(413, str(exc)) from exc
    except FFmpegNotFound as exc:
        raise HTTPException(503, str(exc)) from exc
    except (library.ImportError_, FFmpegError) as exc:
        raise HTTPException(422, f"vidéo refusée : {exc}") from exc
    finally:
        file.file.close()
    try:
        movie, created = ctx.writer.run_sync(lambda ss: library.register_staged(ss, staged, s, title, year))
    except (WriterBusy, WriterClosed) as exc:
        staged.temp_path.unlink(missing_ok=True)
        raise HTTPException(503, str(exc)) from exc
    if not created:
        staged.temp_path.unlink(missing_ok=True)
    if created or not movie.thumbnail_name:
        try:
            name = ctx.io.call(library.make_thumbnail, movie, s)
            movie = ctx.writer.run_sync(lambda ss: repo.update_movie(ss, movie.id, thumbnail_name=name))
        except FFmpegError:
            pass  # pas de vignette : l'interface affiche un visuel neutre
    job = None
    if movie.status == "indexed" and movie.index_version == s.index_version:
        message = "Ce fichier est déjà dans la bibliothèque et indexé : aucun recalcul."
        response.status_code = 200  # aucun job créé
    else:
        try:
            job_state, _ = ctx.jobs.submit_index(movie.id, movie.title)
            job = JobOut(**job_state)
        except (QueueFull, NotAccepting) as exc:
            raise HTTPException(503, str(exc)) from exc
        message = "Film importé, indexation lancée." if created else "Fichier déjà présent : indexation (reprise)."
    return ImportOut(movie=MovieOut.from_model(movie, job.id if job else None), job=job, created=created,
                     message=message)


def _get_movie_or_404(ctx, movie_id: int):  # noqa: ANN001, ANN202
    movie = ctx.read(lambda s: repo.get_movie(s, movie_id))
    if movie is None:
        raise HTTPException(404, "film introuvable")
    return movie


@router.get("/movies/{movie_id}", response_model=MovieDetailOut, summary="Détail d'un film et de ses segments")
def get_movie(request: Request, movie_id: int) -> MovieDetailOut:
    ctx = _ctx(request)
    movie = _get_movie_or_404(ctx, movie_id)
    segs = ctx.read(lambda s: repo.movie_segments(s, movie_id))
    base = MovieOut.from_model(movie, ctx.jobs.active_job_id(movie_id))
    return MovieDetailOut(**base.model_dump(), segments=[SegmentOut.from_model(x) for x in segs])


@router.patch("/movies/{movie_id}", response_model=MovieOut, summary="Modifier le titre ou l'année")
def update_movie(request: Request, movie_id: int, body: MovieUpdateIn) -> MovieOut:
    ctx = _ctx(request)
    _get_movie_or_404(ctx, movie_id)
    fields: dict = {}
    if body.title is not None:
        fields["title"] = body.title.strip()
    if body.clear_year:
        fields["year"] = None
    elif body.year is not None:
        fields["year"] = body.year
    movie = ctx.writer.run_sync(lambda s: repo.update_movie(s, movie_id, **fields))
    return MovieOut.from_model(movie, ctx.jobs.active_job_id(movie_id))


@router.delete("/movies/{movie_id}", response_model=DeleteOut, summary="Suppression cohérente d'un film")
def delete_movie(request: Request, movie_id: int) -> DeleteOut:
    ctx = _ctx(request)
    movie = _get_movie_or_404(ctx, movie_id)
    job_id = ctx.jobs.active_job_id(movie_id)
    if job_id is not None:  # annulation coopérative puis attente de la fin du job (threading.Event)
        ctx.jobs.cancel(job_id)
        snap = ctx.jobs.wait_until_terminal(job_id, timeout=120)
        if snap and snap["state"] not in ("completed", "failed", "cancelled"):
            raise HTTPException(409, "indexation encore en cours d'arrêt, réessayez dans quelques secondes")
    stored = movie.stored_name
    ctx.writer.run_sync(lambda s: repo.delete_movie(s, movie_id))  # cascade : segments, empreintes, bandes
    removed = ctx.io.call(library.delete_movie_files, movie_id, stored, ctx.settings)
    return DeleteOut(deleted=True, movie_id=movie_id, files_removed=removed)


@router.post("/movies/{movie_id}/reindex", response_model=ReindexOut, status_code=202,
             summary="Relancer l'indexation (incrémentale ; force=true pour tout recalculer)")
def reindex_movie(request: Request, response: Response, movie_id: int, force: bool = False) -> ReindexOut:
    ctx = _ctx(request)
    s = ctx.settings
    movie = _get_movie_or_404(ctx, movie_id)
    if not force and movie.status == "indexed" and movie.index_version == s.index_version:
        response.status_code = 200  # rien à faire : pas de job
        return ReindexOut(movie=MovieOut.from_model(movie), job=None,
                          message="Index déjà à jour (même fichier, même version d'algorithme) : rien à recalculer.")
    try:
        job, created = ctx.jobs.submit_index(movie_id, movie.title, force=force)
    except (QueueFull, NotAccepting) as exc:
        raise HTTPException(503, str(exc)) from exc
    return ReindexOut(movie=MovieOut.from_model(movie, job["id"]), job=JobOut(**job),
                      message="Indexation lancée." if created else "Une indexation de ce film est déjà en cours.")


# ----------------------------------------------------------------------- recherche
# POST /api/search : enregistrée par register_upload_routes (limite de taille propre)
def start_search(request: Request, file: Annotated[UploadFile, File(description="clip de 5 à 30 s")],
                       method: Annotated[Literal["bands", "exhaustive"], Form()] = "bands") -> SearchStartOut:
    ctx = _ctx(request)
    s = ctx.settings
    try:
        library.safe_extension(file.filename or "")
        dest = s.temp_dir / f"clip-{uuid.uuid4().hex}.part"

        def _save() -> None:
            from app.concurrency.thread_io import copy_stream_with_hash
            copy_stream_with_hash(file.file, dest, s.max_clip_bytes)

        ctx.io.call(_save)
    except UploadTooLarge as exc:
        raise HTTPException(413, str(exc)) from exc
    except library.ImportError_ as exc:
        raise HTTPException(422, str(exc)) from exc
    finally:
        file.file.close()
    try:
        job = ctx.jobs.submit_search(dest, library.clean_filename(file.filename or "clip"), method)
    except NotAccepting as exc:
        dest.unlink(missing_ok=True)
        raise HTTPException(503, str(exc)) from exc
    return SearchStartOut(search_id=job["search_id"] or job["id"], job=JobOut(**job))


def _search_out(ctx, row) -> SearchOut:  # noqa: ANN001
    return SearchOut(id=row.id, job_id=row.job_id, status=row.status, clip_name=row.clip_name, method=row.method,
                     query_duration_ms=row.query_duration_ms, best_movie_id=row.best_movie_id, start_ms=row.start_ms,
                     end_ms=row.end_ms, score=row.score, elapsed_ms=row.elapsed_ms, created_at=row.created_at,
                     result=json.loads(row.result_json or "{}"), job=_job_out(ctx, row.job_id))


@router.get("/search/{search_id}", response_model=SearchOut, summary="Statut et résultat d'une recherche")
def get_search(request: Request, search_id: str) -> SearchOut:
    ctx = _ctx(request)
    row = ctx.read(lambda s: repo.get_search(s, search_id))
    if row is None:
        raise HTTPException(404, "recherche introuvable")
    return _search_out(ctx, row)


@router.get("/searches", response_model=list[SearchOut], summary="Dernières recherches")
def list_searches(request: Request, limit: int = Query(20, ge=1, le=200)) -> list[SearchOut]:
    ctx = _ctx(request)
    rows = ctx.read(lambda s: repo.list_searches(s, limit))
    return [_search_out(ctx, r) for r in rows]


# ----------------------------------------------------------------------- jobs
@router.get("/jobs", response_model=list[JobOut], summary="Jobs locaux (actifs + historique)")
def list_jobs(request: Request, kind: str | None = None, limit: int = Query(100, ge=1, le=500)) -> list[JobOut]:
    ctx = _ctx(request)
    rows = ctx.read(lambda s: repo.list_jobs(s, limit, kind))
    out: dict[str, dict] = {r.id: job_row_to_dict(r) for r in rows}
    for snap in ctx.jobs.snapshots():  # l'état en mémoire est le plus frais pour les jobs actifs
        if (kind is None or snap["kind"] == kind) and (snap["id"] in out or snap["state"] in ("queued", "running")):
            out[snap["id"]] = snap
    ordered = sorted(out.values(), key=lambda j: j["created_at"], reverse=True)[:limit]
    return [JobOut(**j) for j in ordered]


@router.get("/jobs/{job_id}", response_model=JobOut, summary="État et progression réels d'un job")
def get_job(request: Request, job_id: str) -> JobOut:
    ctx = _ctx(request)
    snap = ctx.jobs.snapshot(job_id)
    if snap is not None:
        return JobOut(**snap)
    row = ctx.read(lambda s: repo.get_job(s, job_id))
    if row is None:
        raise HTTPException(404, "job introuvable")
    return JobOut(**job_row_to_dict(row))


@router.post("/jobs/{job_id}/cancel", response_model=JobOut, summary="Annulation coopérative")
def cancel_job(request: Request, job_id: str) -> JobOut:
    ctx = _ctx(request)
    snap = ctx.jobs.cancel(job_id)
    if snap is None:
        raise HTTPException(404, "job introuvable ou déjà archivé")
    return JobOut(**snap)


# ----------------------------------------------------------------------- benchmarks
@router.get("/benchmarks", summary="Résultats de benchmarks enregistrés (vraies mesures)")
def list_benchmarks(request: Request) -> dict:
    ctx = _ctx(request)
    rows = ctx.read(lambda s: repo.list_benchmarks(s))
    runs: dict[str, dict] = {}
    for r in rows:
        run = runs.setdefault(r.run_id, {"run_id": r.run_id, "created_at": r.created_at.isoformat() + "Z",
                                         "dataset_id": r.dataset_id, "index": [], "recall": None, "machine": None})
        metrics = json.loads(r.metrics_json or "{}")
        if r.kind == "index":
            run["machine"] = metrics.pop("machine", run["machine"])
            run["corpus"] = metrics.pop("corpus", None)
            run["segments"] = metrics.pop("segments", None)
            run["index"].append(metrics)
        elif r.kind == "search":
            run["recall"] = metrics
    for run in runs.values():
        run["index"].sort(key=lambda x: (x["mode"] != "sequential", x["process_count"]))
    return {"runs": sorted(runs.values(), key=lambda x: x["created_at"], reverse=True),
            "machine": bench.machine_info(ctx.settings)}


@router.post("/benchmarks/run", response_model=JobOut, status_code=202, summary="Lancer un benchmark local")
def run_benchmark(request: Request, body: BenchmarkRunIn) -> JobOut:
    ctx = _ctx(request)
    s = ctx.settings
    cpus = __import__("os").cpu_count() or 1
    modes = sorted({m for m in body.modes if 0 <= m <= cpus})
    if not modes:
        raise HTTPException(422, "aucun mode valide")
    movies = ctx.read(lambda ss: [m for m in repo.list_movies(ss, status="indexed")
                                        if body.movie_ids is None or m.id in body.movie_ids])
    if not movies:
        raise HTTPException(422, "aucun film indexé : importez et indexez au moins un film")
    items = [bench.CorpusItem(library.movie_path(m, s), m.duration_ms, m.title, m.content_hash) for m in movies]
    ds = bench.dataset_id(items, s)
    run_id = uuid.uuid4().hex

    def runner(publish):  # noqa: ANN001, ANN202 - exécuté par le thread du benchmark
        machine = bench.machine_info(s)
        result = bench.run_index_benchmark(items, s, modes, body.repeats, publish)
        rows = bench.summarize_for_db(run_id, ds, result, machine)
        if body.include_recall:
            publish({"stage": "rappel de l'index par bandes vs exhaustive"})
            recall = bench.run_band_recall_benchmark(ctx.session_factory, s, ctx.params)
            rows.append({"run_id": run_id, "kind": "search", "mode": "bands-recall", "process_count": 1,
                         "dataset_id": ds, "elapsed_ms": recall.get("exhaustive_ms_same_queries", 0) or 0,
                         "metrics_json": json.dumps(recall)})
        ctx.writer.run_sync(lambda ss: repo.add_benchmark_rows(ss, rows))
        return {"run_id": run_id}

    try:
        job = ctx.jobs.submit_benchmark(f"Benchmark {len(items)} film(s), modes {modes}", runner)
    except (QueueFull, NotAccepting) as exc:
        raise HTTPException(409, str(exc)) from exc
    return JobOut(**job)


@router.get("/benchmarks/export", summary="Export CSV ou JSON des benchmarks")
def export_benchmarks(request: Request, format: Literal["csv", "json"] = "json",
                            run_id: str | None = None) -> Response:
    data = list_benchmarks(request)
    runs = [r for r in data["runs"] if run_id is None or r["run_id"] == run_id]
    if format == "json":
        return JSONResponse({"runs": runs}, headers={"Content-Disposition": "attachment; filename=benchmarks.json"})
    buf = io.StringIO()
    cols = ["run_id", "created_at", "dataset_id", "mode", "process_count", "elapsed_ms_median", "write_ms_median",
            "speedup", "efficiency", "frames_per_s", "fingerprints_per_s", "peak_ram_mb", "busy_cores_avg",
            "identical_to_sequential"]
    w = csv.writer(buf)
    w.writerow(cols)
    for r in runs:
        for x in r["index"]:
            w.writerow([r["run_id"], r["created_at"], r["dataset_id"], *[x.get(c) for c in cols[3:]]])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=benchmarks.csv"})


# ----------------------------------------------------------------------- médias
@router.get("/media/{movie_id}/thumbnail", summary="Vignette d'un film géré")
def thumbnail(request: Request, movie_id: int) -> FileResponse:
    ctx = _ctx(request)
    movie = _get_movie_or_404(ctx, movie_id)
    try:
        name = movie.thumbnail_name or ctx.io.call(library.make_thumbnail, movie, ctx.settings)
        path = safe_child(ctx.settings.thumbs_dir, name)
        if not path.exists():
            name = ctx.io.call(library.make_thumbnail, movie, ctx.settings)
            path = safe_child(ctx.settings.thumbs_dir, name)
    except (FFmpegError, ValueError) as exc:
        raise HTTPException(404, f"vignette indisponible : {exc}") from exc
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "max-age=300"})


@router.get("/media/{movie_id}/frame", summary="Image d'un film à un instant donné")
def frame(request: Request, movie_id: int, ms: int = Query(..., ge=0)) -> FileResponse:
    ctx = _ctx(request)
    movie = _get_movie_or_404(ctx, movie_id)
    try:
        path = ctx.io.call(library.frame_image, movie, ms, ctx.settings)
    except (FFmpegError, ValueError) as exc:
        raise HTTPException(404, f"image indisponible : {exc}") from exc
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "max-age=3600"})


@router.get("/media/{movie_id}/video", summary="Lecture du film (requêtes Range supportées)")
def video(request: Request, movie_id: int) -> FileResponse:
    ctx = _ctx(request)
    movie = _get_movie_or_404(ctx, movie_id)
    try:
        path = library.movie_path(movie, ctx.settings)
    except ValueError as exc:
        raise HTTPException(404, "fichier indisponible") from exc
    if not path.exists():
        raise HTTPException(404, "fichier vidéo introuvable")
    return FileResponse(path, media_type=library.media_type_for(movie.stored_name))
