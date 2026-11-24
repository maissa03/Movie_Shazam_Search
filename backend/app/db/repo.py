from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import delete, func, insert, or_, select, update
from sqlalchemy.orm import Session

from app.db.models import Fingerprint, FingerprintBand, Job, Movie, Search, Segment, utcnow
from app.video.fingerprint import bands, to_blob
from app.video.pipeline import SegmentResult
from app.video.segmentation import SegmentPlan

BAND_MIN_INFORMATIVENESS = 2.0
MOVIE_STATUSES = ("pending", "indexing", "indexed", "failed", "cancelled")
# Types de jobs gérés par l'application. Les anciennes lignes kind="benchmark" restent dans SQLite
# mais ne sont plus lues ni modifiées.
JOB_KINDS = ("index", "search")


def get_movie(s: Session, movie_id: int) -> Movie | None:
    return s.get(Movie, movie_id)


def find_movie_by_hash(s: Session, content_hash: str) -> Movie | None:
    return s.scalars(select(Movie).where(Movie.content_hash == content_hash)).first()


def list_movies(s: Session, q: str | None = None, sort: str = "created_at", order: str = "desc",
                status: str | None = None) -> list[Movie]:
    stmt = select(Movie)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Movie.title.ilike(like), Movie.original_filename.ilike(like)))
    if status:
        stmt = stmt.where(Movie.status == status)
    column = {"title": func.lower(Movie.title), "year": Movie.year, "duration": Movie.duration_ms,
              "size": Movie.file_size, "status": Movie.status}.get(sort, Movie.created_at)
    stmt = stmt.order_by(column.asc() if order == "asc" else column.desc(), Movie.id.desc())
    return list(s.scalars(stmt))


def create_movie(s: Session, **fields) -> Movie:
    movie = Movie(**fields)
    s.add(movie)
    s.flush()
    return movie


def update_movie(s: Session, movie_id: int, **fields) -> Movie | None:
    movie = s.get(Movie, movie_id)
    if movie is None:
        return None
    for k, v in fields.items():
        setattr(movie, k, v)
    s.flush()
    return movie


def clear_movie_index(s: Session, movie_id: int) -> None:
    s.execute(delete(Segment).where(Segment.movie_id == movie_id))
    s.execute(update(Movie).where(Movie.id == movie_id).values(
        segments_total=0, segments_done=0, fingerprints_count=0, index_version=None, segment_ms=None,
        indexed_at=None))


def delete_movie(s: Session, movie_id: int) -> Movie | None:
    movie = s.get(Movie, movie_id)
    if movie is None:
        return None
    s.execute(delete(Movie).where(Movie.id == movie_id))
    return movie


@dataclass(frozen=True)
class SegmentTodo:
    segment_id: int
    segment_index: int
    start_ms: int
    end_ms: int


def prepare_index(s: Session, movie_id: int, version: str, segment_ms: int, plans: list[SegmentPlan],
                  force: bool = False) -> list[SegmentTodo] | None:
    movie = s.get(Movie, movie_id)
    if movie is None:
        return None
    existing = list(s.scalars(select(Segment).where(Segment.movie_id == movie_id)))
    compatible = (not force and movie.index_version == version and movie.segment_ms == segment_ms
                  and existing and all(seg.index_version == version for seg in existing)
                  and {(x.start_ms, x.end_ms) for x in existing} == {(p.start_ms, p.end_ms) for p in plans})
    if not compatible:
        clear_movie_index(s, movie_id)
        s.add_all(Segment(movie_id=movie_id, index_version=version, segment_index=p.index, start_ms=p.start_ms,
                          end_ms=p.end_ms, status="pending") for p in plans)
        s.flush()
        existing = list(s.scalars(select(Segment).where(Segment.movie_id == movie_id)))
    else:
        for seg in existing:
            if seg.status != "done":
                s.execute(delete(Fingerprint).where(Fingerprint.segment_id == seg.id))
                seg.status, seg.error, seg.fingerprints_count = "pending", None, 0
    done = [x for x in existing if x.status == "done"]
    movie.index_version = version
    movie.segment_ms = segment_ms
    movie.segments_total = len(existing)
    movie.segments_done = len(done)
    movie.fingerprints_count = sum(x.fingerprints_count for x in done)
    movie.status = "indexing"
    movie.error = None
    s.flush()
    return [SegmentTodo(x.id, x.segment_index, x.start_ms, x.end_ms)
            for x in sorted(existing, key=lambda x: x.start_ms) if x.status != "done"]


def save_segment_result(s: Session, movie_id: int, segment_id: int, version: str, result: SegmentResult,
                        job_id: str | None = None) -> bool:
    seg = s.get(Segment, segment_id)
    if seg is None or seg.movie_id != movie_id or seg.index_version != version:
        return False
    s.execute(delete(Fingerprint).where(Fingerprint.segment_id == segment_id))
    rows = [{"movie_id": movie_id, "segment_id": segment_id, "timestamp_ms": ts, "hash_blob": to_blob(h),
             "informativeness": inf, "algorithm_version": version}
            for ts, h, inf in zip(result.timestamps, result.hashes, result.informativeness)]
    if rows:
        s.execute(insert(Fingerprint), rows)
        ids = {ts: fid for ts, fid in s.execute(select(Fingerprint.timestamp_ms, Fingerprint.id)
                                                 .where(Fingerprint.segment_id == segment_id))}
        band_rows = []
        for ts, h, inf in zip(result.timestamps, result.hashes, result.informativeness):
            if inf >= BAND_MIN_INFORMATIVENESS:
                fid = ids[ts]
                band_rows.extend({"band_index": i, "band_value": v, "fingerprint_id": fid}
                                 for i, v in enumerate(bands(h)))
        if band_rows:
            s.execute(insert(FingerprintBand), band_rows)
    was_done = seg.status == "done"
    seg.status = "done"
    seg.error = None
    seg.fingerprints_count = len(rows)
    seg.frames_decoded = result.frames_decoded
    seg.processing_ms = int(result.wall_s * 1000)
    seg.worker_pid = result.pid
    movie = s.get(Movie, movie_id)
    if movie is not None and not was_done:
        movie.segments_done += 1
        movie.fingerprints_count += len(rows)
    if job_id:
        job = s.get(Job, job_id)
        if job is not None:
            job.completed_units = movie.segments_done if movie else job.completed_units + 1
            job.fingerprints_count = movie.fingerprints_count if movie else job.fingerprints_count
            job.frames_decoded += result.frames_decoded
            pids = set(json.loads(job.worker_pids or "[]")) | {result.pid}
            job.worker_pids = json.dumps(sorted(pids))
            job.workers_used = len(pids)
    return True


def mark_segment_failed(s: Session, segment_id: int, error: str) -> None:
    s.execute(update(Segment).where(Segment.id == segment_id).values(status="failed", error=error[:2000]))


def finalize_index(s: Session, movie_id: int, version: str) -> tuple[bool, str]:
    movie = s.get(Movie, movie_id)
    if movie is None:
        return False, "film supprimé"
    segs = list(s.scalars(select(Segment).where(Segment.movie_id == movie_id)))
    not_done = [x for x in segs if x.status != "done" or x.index_version != version]
    n_fp = s.scalar(select(func.count()).select_from(Fingerprint).where(
        Fingerprint.movie_id == movie_id, Fingerprint.algorithm_version == version)) or 0
    expected = sum(x.fingerprints_count for x in segs)
    if not segs or not_done or n_fp != expected or n_fp == 0:
        movie.status = "failed"
        movie.error = (f"index incomplet : {len(not_done)} segment(s) non terminé(s), "
                       f"{n_fp} empreintes pour {expected} attendues")
        return False, movie.error
    movie.status = "indexed"
    movie.error = None
    movie.segments_done = len(segs)
    movie.fingerprints_count = n_fp
    movie.indexed_at = utcnow()
    return True, "ok"


def movie_segments(s: Session, movie_id: int) -> list[Segment]:
    return list(s.scalars(select(Segment).where(Segment.movie_id == movie_id).order_by(Segment.start_ms)))


def movie_fingerprints(s: Session, movie_id: int, version: str) -> list[tuple[int, bytes, float]]:
    return [(ts, blob, inf) for ts, blob, inf in s.execute(
        select(Fingerprint.timestamp_ms, Fingerprint.hash_blob, Fingerprint.informativeness)
        .where(Fingerprint.movie_id == movie_id, Fingerprint.algorithm_version == version)
        .order_by(Fingerprint.timestamp_ms))]


def upsert_job(s: Session, job_id: str, **fields) -> Job:
    job = s.get(Job, job_id)
    if job is None:
        job = Job(id=job_id, **fields)
        s.add(job)
    else:
        for k, v in fields.items():
            setattr(job, k, v)
    s.flush()
    return job


def get_job(s: Session, job_id: str) -> Job | None:
    return s.get(Job, job_id)


def list_jobs(s: Session, limit: int = 100, kind: str | None = None) -> list[Job]:
    stmt = select(Job).where(Job.kind.in_(JOB_KINDS)).order_by(Job.created_at.desc()).limit(limit)
    if kind:
        stmt = stmt.where(Job.kind == kind)
    return list(s.scalars(stmt))


def fail_interrupted_jobs(s: Session) -> int:
    jobs = list(s.scalars(select(Job).where(Job.kind.in_(JOB_KINDS), Job.state.in_(("queued", "running")))))
    for job in jobs:
        job.state = "failed"
        job.error = "interrompu par l'arrêt de l'application (les segments terminés sont conservés)"
        job.finished_at = utcnow()
    movies = list(s.scalars(select(Movie).where(Movie.status == "indexing")))
    for m in movies:
        m.status = "cancelled"
        m.error = "indexation interrompue par l'arrêt de l'application : relancez-la pour reprendre"
    s.execute(update(Search).where(Search.status.in_(("queued", "running"))).values(
        status="failed", result_json=json.dumps({"error": "interrompu par l'arrêt de l'application"})))
    return len(jobs)


def prune_jobs(s: Session, keep_days: int = 30) -> None:
    s.execute(delete(Job).where(Job.kind.in_(JOB_KINDS), Job.finished_at < utcnow() - timedelta(days=keep_days)))


def save_search(s: Session, **fields) -> Search:
    search = s.get(Search, fields["id"])
    if search is None:
        search = Search(**fields)
        s.add(search)
    else:
        for k, v in fields.items():
            setattr(search, k, v)
    s.flush()
    return search


def get_search(s: Session, search_id: str) -> Search | None:
    return s.get(Search, search_id)


def list_searches(s: Session, limit: int = 20) -> list[Search]:
    return list(s.scalars(select(Search).order_by(Search.created_at.desc()).limit(limit)))


def library_stats(s: Session) -> dict:
    total = s.scalar(select(func.count()).select_from(Movie)) or 0
    indexed = s.scalar(select(func.count()).select_from(Movie).where(Movie.status == "indexed")) or 0
    indexed_ms = s.scalar(select(func.coalesce(func.sum(Movie.duration_ms), 0)).where(Movie.status == "indexed"))
    fps = s.scalar(select(func.coalesce(func.sum(Movie.fingerprints_count), 0)).where(Movie.status == "indexed"))
    by_status = {st: n for st, n in s.execute(select(Movie.status, func.count()).group_by(Movie.status))}
    n_searches = s.scalar(select(func.count()).select_from(Search)) or 0
    return {"movies_total": total, "movies_indexed": indexed, "indexed_duration_ms": int(indexed_ms or 0),
            "fingerprints_total": int(fps or 0), "movies_by_status": by_status, "searches_total": n_searches}
