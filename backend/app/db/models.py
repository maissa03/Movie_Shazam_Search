"""Schéma SQLite (SQLAlchemy 2.x).  Initialisation : ``Base.metadata.create_all`` (voir ``session.init_db``).

Choix notables :
  * ``movies.stored_name`` : nom **relatif** au dossier des médias, jamais un chemin absolu
    (le navigateur ne voit jamais un chemin du PC, et rien ne pointe hors du stockage) ;
  * ``fingerprints.hash_blob`` : 8 octets big-endian (un INTEGER SQLite est signé 64 bits
    et ne peut pas représenter les empreintes >= 2^63) ;
  * ``UNIQUE(movie_id, timestamp_ms, algorithm_version)`` : impossible d'insérer deux fois
    la même empreinte, ni de mélanger deux versions au même instant ;
  * ``fingerprint_bands`` en ``WITHOUT ROWID`` avec clé primaire
    ``(band_index, band_value, fingerprint_id)`` : la table **est** l'index
    ``(band_index, band_value)`` demandé (B-tree compact, recherche par préfixe).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Movie(Base):
    __tablename__ = "movies"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    original_filename: Mapped[str] = mapped_column(String(300))
    stored_name: Mapped[str] = mapped_column(String(120), unique=True)
    file_size: Mapped[int] = mapped_column(Integer)
    file_mtime_ns: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    duration_ms: Mapped[int] = mapped_column(Integer)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    codec: Mapped[str] = mapped_column(String(40))
    fps: Mapped[float] = mapped_column(Float)
    format_name: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    index_version: Mapped[str | None] = mapped_column(String(60), nullable=True)
    segment_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    segments_total: Mapped[int] = mapped_column(Integer, default=0)
    segments_done: Mapped[int] = mapped_column(Integer, default=0)
    fingerprints_count: Mapped[int] = mapped_column(Integer, default=0)
    thumbnail_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    indexed_at: Mapped[datetime | None] = mapped_column(nullable=True)


class Segment(Base):
    __tablename__ = "segments"
    __table_args__ = (UniqueConstraint("movie_id", "index_version", "start_ms", name="uq_segment"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    movie_id: Mapped[int] = mapped_column(ForeignKey("movies.id", ondelete="CASCADE"), index=True)
    index_version: Mapped[str] = mapped_column(String(60))
    segment_index: Mapped[int] = mapped_column(Integer)
    start_ms: Mapped[int] = mapped_column(Integer)
    end_ms: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | done | failed
    fingerprints_count: Mapped[int] = mapped_column(Integer, default=0)
    frames_decoded: Mapped[int] = mapped_column(Integer, default=0)
    processing_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    worker_pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class Fingerprint(Base):
    __tablename__ = "fingerprints"
    __table_args__ = (
        UniqueConstraint("movie_id", "timestamp_ms", "algorithm_version", name="uq_fingerprint"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    movie_id: Mapped[int] = mapped_column(ForeignKey("movies.id", ondelete="CASCADE"))
    segment_id: Mapped[int] = mapped_column(ForeignKey("segments.id", ondelete="CASCADE"), index=True)
    timestamp_ms: Mapped[int] = mapped_column(Integer)
    hash_blob: Mapped[bytes] = mapped_column(LargeBinary(8))
    informativeness: Mapped[float] = mapped_column(Float)
    algorithm_version: Mapped[str] = mapped_column(String(60))


class FingerprintBand(Base):
    __tablename__ = "fingerprint_bands"
    __table_args__ = ({"sqlite_with_rowid": False},)

    band_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    band_value: Mapped[int] = mapped_column(Integer, primary_key=True)
    fingerprint_id: Mapped[int] = mapped_column(
        ForeignKey("fingerprints.id", ondelete="CASCADE"), primary_key=True
    )


Index("ix_fingerprint_bands_fp", FingerprintBand.fingerprint_id)  # suppression en cascade rapide


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))  # index | search | benchmark
    movie_id: Mapped[int | None] = mapped_column(ForeignKey("movies.id", ondelete="SET NULL"), nullable=True)
    label: Mapped[str] = mapped_column(String(300), default="")
    state: Mapped[str] = mapped_column(String(20), index=True)  # queued | running | completed | failed | cancelled
    stage: Mapped[str | None] = mapped_column(String(120), nullable=True)
    completed_units: Mapped[int] = mapped_column(Integer, default=0)
    total_units: Mapped[int] = mapped_column(Integer, default=0)
    fingerprints_count: Mapped[int] = mapped_column(Integer, default=0)
    frames_decoded: Mapped[int] = mapped_column(Integer, default=0)
    workers_used: Mapped[int] = mapped_column(Integer, default=0)
    worker_pids: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    details_json: Mapped[str] = mapped_column(Text, default="{}")


class Search(Base):
    __tablename__ = "searches"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    job_id: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(20))  # running | match | ambiguous | no_match | insufficient | failed
    clip_name: Mapped[str] = mapped_column(String(300))
    method: Mapped[str] = mapped_column(String(20), default="bands")
    query_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    best_movie_id: Mapped[int | None] = mapped_column(ForeignKey("movies.id", ondelete="SET NULL"), nullable=True)
    start_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    elapsed_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Benchmark(Base):
    __tablename__ = "benchmarks"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="index")  # index | search
    mode: Mapped[str] = mapped_column(String(30))  # sequential | parallel | bands | exhaustive
    process_count: Mapped[int] = mapped_column(Integer)
    dataset_id: Mapped[str] = mapped_column(String(64))
    elapsed_ms: Mapped[float] = mapped_column(Float)
    frames_count: Mapped[int] = mapped_column(Integer, default=0)
    peak_ram_mb: Mapped[float | None] = mapped_column(Float, nullable=True)
    metrics_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
