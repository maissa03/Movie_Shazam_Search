"""Configuration centralisée (variables d'environnement / fichier ``backend/.env``).

Les chemins relatifs sont résolus depuis la **racine du projet** (pas depuis le dossier
courant), pour que ``uvicorn``, ``pytest`` et les scripts utilisent les mêmes données.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.video.fingerprint import ALGORITHM_NAME

BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_ROOT = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", env_file_encoding="utf-8", extra="ignore")

    # --- serveur (local uniquement) ---
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    frontend_port: int = 3000
    frontend_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://127.0.0.1:3000"]
    )

    # --- stockage local ---
    database_url: str = "sqlite:///./data/movieshazam.db"
    media_root: Path = Path("./data/media")
    temp_root: Path = Path("./data/temp")

    # --- empreintes ---
    fingerprint_version: str = ALGORITHM_NAME
    fingerprint_fps: float = 2.0
    query_oversampling: int = 5  # le clip est échantillonné 5x plus finement (même pHash)
    segment_duration_seconds: float = 180.0  # longueur MAXIMALE d'un segment
    min_segment_seconds: float = 10.0
    target_min_segments: int = 8
    segment_overlap_ms: int = 0
    ffmpeg_threads: int = 1  # threads internes de CHAQUE FFmpeg (évite la sursouscription)
    segment_timeout_seconds: float = 900.0

    # --- concurrence locale ---
    process_workers: int = 2
    io_threads: int = 2
    max_active_index_jobs: int = 1
    max_active_search_jobs: int = 2
    max_queued_jobs: int = 32
    max_inflight_segments_per_worker: int = 2

    # --- limites ---
    min_query_duration_seconds: float = 4.0
    max_query_duration_seconds: float = 30.0
    max_upload_gb: float = 8.0
    max_clip_upload_mb: float = 300.0

    @field_validator("process_workers", "io_threads", "max_active_index_jobs", "max_active_search_jobs")
    @classmethod
    def _positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("doit être >= 1")
        return v

    @field_validator("app_host")
    @classmethod
    def _local_only(cls, v: str) -> str:
        if v in ("0.0.0.0", "::"):
            raise ValueError("MovieShazam écoute uniquement en local (127.0.0.1)")
        return v

    # ------------------------------------------------------------------ chemins
    def _resolve(self, p: Path) -> Path:
        return (p if p.is_absolute() else PROJECT_ROOT / p).resolve()

    @property
    def media_dir(self) -> Path:
        return self._resolve(self.media_root)

    @property
    def movies_dir(self) -> Path:
        return self.media_dir / "movies"

    @property
    def thumbs_dir(self) -> Path:
        return self.media_dir / "thumbs"

    @property
    def temp_dir(self) -> Path:
        return self._resolve(self.temp_root)

    @property
    def database_path(self) -> Path:
        prefix = "sqlite:///"
        if not self.database_url.startswith(prefix):
            raise ValueError("seul SQLite local est supporté (DATABASE_URL=sqlite:///...)")
        return self._resolve(Path(self.database_url[len(prefix):]))

    @property
    def index_version(self) -> str:
        """Version effective de l'index : algorithme + paramètres qui changent les empreintes.

        Changer ``FINGERPRINT_FPS`` produit donc une autre version : les anciens index ne
        sont jamais mélangés avec les nouveaux.
        """
        fps = f"{self.fingerprint_fps:g}"
        return f"{self.fingerprint_version}@{fps}fps"

    @property
    def max_upload_bytes(self) -> int:
        return int(self.max_upload_gb * 1024**3)

    @property
    def max_clip_bytes(self) -> int:
        return int(self.max_clip_upload_mb * 1024**2)

    def ensure_dirs(self) -> None:
        for d in (self.media_dir, self.movies_dir, self.thumbs_dir, self.temp_dir, self.database_path.parent):
            d.mkdir(parents=True, exist_ok=True)

    def effective_workers(self) -> int:
        return max(1, min(self.process_workers, os.cpu_count() or 1))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
