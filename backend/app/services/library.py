"""Bibliothèque locale : import contrôlé, déduplication, vignettes, vérification, suppression.

Toutes les fonctions de ce module sont **bloquantes** (disque, ffprobe, FFmpeg) : l'API les
exécute dans le ``IOExecutor`` (pool de threads borné à ``IO_THREADS``) pour ne jamais avoir
plus de quelques opérations disque lourdes en parallèle.

Import en 3 temps (aucun fichier n'est chargé entièrement en RAM) :
  1. ``stage_stream`` : copie par blocs de 1 Mo vers ``data/temp`` + SHA-256 au passage + ffprobe ;
  2. ``register_staged`` (dans le thread écrivain SQLite) : si ce contenu existe déjà -> on
     renvoie le film existant (indexation incrémentale) ; sinon renommage **atomique**
     ``os.replace`` vers ``data/media/movies/<sha256[:24]>.<ext>`` puis insertion de la ligne.
     Vérification + insertion s'exécutent dans le même thread écrivain : deux imports
     simultanés du même fichier ne peuvent pas créer deux films (pas de condition de course) ;
  3. vignette générée à part.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from sqlalchemy.orm import Session, sessionmaker

from app.concurrency.thread_io import KeyedLocks, copy_stream_with_hash, safe_child, sha256_file
from app.core.settings import Settings
from app.db import repo
from app.db.models import Movie
from app.db.writer import DbWriter
from app.video.ffmpeg_io import FFmpegError, VideoInfo, extract_thumbnail, probe_video

log = logging.getLogger("movieshazam.library")

ALLOWED_EXTENSIONS = {".mp4", ".m4v", ".mkv", ".mov", ".avi", ".webm", ".mpg", ".mpeg", ".ts", ".wmv", ".flv",
                      ".ogv", ".3gp"}
MEDIA_TYPES = {".mp4": "video/mp4", ".m4v": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime",
               ".mkv": "video/x-matroska", ".ogv": "video/ogg"}
_thumb_locks = KeyedLocks()


class ImportError_(ValueError):
    """Erreur d'import présentable à l'utilisateur."""


@dataclass
class StagedFile:
    temp_path: Path
    size: int
    sha256: str
    original_name: str
    info: VideoInfo


def safe_extension(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ImportError_(f"extension non prise en charge ({ext or 'aucune'}) ; formats : "
                           + ", ".join(sorted(ALLOWED_EXTENSIONS)))
    return ext


def clean_filename(filename: str) -> str:
    """Nom d'origine nettoyé (affichage uniquement, jamais utilisé comme chemin)."""
    name = Path(filename.replace("\\", "/")).name
    name = re.sub(r"[\x00-\x1f<>:\"|?*]", "_", name).strip() or "video"
    return name[:200]


def title_from_filename(filename: str) -> str:
    stem = Path(clean_filename(filename)).stem
    stem = re.sub(r"[._]+", " ", stem)
    return re.sub(r"\s+", " ", stem).strip()[:200] or "Sans titre"


def check_disk_space(directory: Path, needed_bytes: int, margin: int = 512 * 1024**2) -> None:
    free = shutil.disk_usage(directory).free
    if free < needed_bytes + margin:
        raise ImportError_(f"espace disque insuffisant ({free / 1024**3:.1f} Go libres)")


def stage_stream(src: BinaryIO, original_name: str, settings: Settings, max_bytes: int) -> StagedFile:
    """Copie en flux vers un fichier temporaire, calcule le SHA-256 et valide avec ffprobe."""
    name = clean_filename(original_name)
    safe_extension(name)
    temp = settings.temp_dir / f"upload-{uuid.uuid4().hex}.part"
    size, digest = copy_stream_with_hash(src, temp, max_bytes)
    if size == 0:
        temp.unlink(missing_ok=True)
        raise ImportError_("fichier vide")
    try:
        info = probe_video(temp)
    except FFmpegError:
        temp.unlink(missing_ok=True)
        raise
    return StagedFile(temp, size, digest, name, info)


def stage_local_file(path: Path, settings: Settings) -> StagedFile:
    with open(path, "rb") as f:
        return stage_stream(f, path.name, settings, settings.max_upload_bytes)


def register_staged(s: Session, staged: StagedFile, settings: Settings, title: str | None,
                    year: int | None) -> tuple[Movie, bool]:
    """**À exécuter dans le thread écrivain.**  Retourne ``(film, créé)``."""
    existing = repo.find_movie_by_hash(s, staged.sha256)
    if existing is not None:
        return existing, False
    ext = safe_extension(staged.original_name)
    stored_name = f"{staged.sha256[:24]}{ext}"
    final = safe_child(settings.movies_dir, stored_name)
    os.replace(staged.temp_path, final)  # renommage atomique (même volume : data/)
    try:
        st = final.stat()
        movie = repo.create_movie(
            s, title=(title or title_from_filename(staged.original_name)).strip()[:300], year=year,
            original_filename=staged.original_name, stored_name=stored_name, file_size=st.st_size,
            file_mtime_ns=st.st_mtime_ns, content_hash=staged.sha256, duration_ms=staged.info.duration_ms,
            width=staged.info.width, height=staged.info.height, codec=staged.info.codec, fps=staged.info.fps,
            format_name=staged.info.format_name, status="pending")
    except BaseException:
        final.unlink(missing_ok=True)
        raise
    return movie, True


def movie_path(movie: Movie, settings: Settings) -> Path:
    return safe_child(settings.movies_dir, movie.stored_name)


def make_thumbnail(movie: Movie, settings: Settings) -> str:
    """Vignette principale à 10 % de la durée (bloquant)."""
    name = f"movie_{movie.id}.jpg"
    dest = safe_child(settings.thumbs_dir, name)
    at = int(min(max(movie.duration_ms * 0.10, 1000), max(movie.duration_ms - 500, 0)))
    with _thumb_locks.hold(name):
        if not dest.exists():
            extract_thumbnail(movie_path(movie, settings), dest, at)
    return name


def frame_image(movie: Movie, at_ms: int, settings: Settings) -> Path:
    """Image du film à un instant donné (mise en cache, instants arrondis à 500 ms)."""
    at_ms = max(0, min(int(round(at_ms / 500) * 500), max(movie.duration_ms - 100, 0)))
    frames_dir = settings.thumbs_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    name = f"m{movie.id}_{at_ms}.jpg"
    dest = safe_child(frames_dir, name)
    with _thumb_locks.hold(name):  # deux requêtes simultanées -> un seul FFmpeg
        if not dest.exists():
            extract_thumbnail(movie_path(movie, settings), dest, at_ms, width=320)
    return dest


def delete_movie_files(movie_id: int, stored_name: str | None, settings: Settings) -> list[str]:
    """Supprime **uniquement** des fichiers situés dans le stockage de l'application."""
    removed = []
    candidates: list[Path] = []
    if stored_name:
        candidates.append(safe_child(settings.movies_dir, stored_name))
    candidates.append(safe_child(settings.thumbs_dir, f"movie_{movie_id}.jpg"))
    frames_dir = settings.thumbs_dir / "frames"
    if frames_dir.exists():
        candidates += [p for p in frames_dir.glob(f"m{movie_id}_*.jpg") if p.parent.resolve() == frames_dir.resolve()]
    for p in candidates:
        try:
            p.unlink()
            removed.append(p.name)
        except FileNotFoundError:
            pass
    return removed


def verify_library(session_factory: sessionmaker[Session], writer: DbWriter, settings: Settings) -> dict:
    """Au démarrage : détecte les films supprimés ou modifiés depuis l'indexation."""
    report = {"checked": 0, "missing": 0, "modified": 0}
    with session_factory() as s:
        movies = [(m.id, m.stored_name, m.file_size, m.file_mtime_ns, m.content_hash, m.status)
                  for m in repo.list_movies(s)]
    for mid, stored, size, mtime, digest, _status in movies:
        report["checked"] += 1
        try:
            path = safe_child(settings.movies_dir, stored)
        except ValueError:
            path = None
        if path is None or not path.exists():
            report["missing"] += 1
            writer.run_sync(lambda s, m=mid: (repo.clear_movie_index(s, m), repo.update_movie(
                s, m, status="failed", error="fichier vidéo introuvable dans le stockage : index invalidé")))
            continue
        st = path.stat()
        if st.st_size == size and st.st_mtime_ns == mtime:
            continue
        new_digest = sha256_file(path)
        if new_digest == digest:
            writer.run_sync(lambda s, m=mid, st=st: repo.update_movie(s, m, file_mtime_ns=st.st_mtime_ns))
            continue
        report["modified"] += 1
        writer.run_sync(lambda s, m=mid, st=st, d=new_digest: (repo.clear_movie_index(s, m), repo.update_movie(
            s, m, status="pending", content_hash=d, file_size=st.st_size, file_mtime_ns=st.st_mtime_ns,
            error="fichier modifié depuis l'indexation : réindexation nécessaire")))
    return report


def media_type_for(stored_name: str) -> str:
    return MEDIA_TYPES.get(Path(stored_name).suffix.lower(), "application/octet-stream")
