"""Fixtures communes : vraies vidéos générées, réglages isolés, application FastAPI de test.

Aucun test de décodage ou de reconnaissance n'utilise de faux fichiers : les corpus sont
produits par ``scripts/generate_demo_videos.py`` (déterministe) puis réutilisés.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
sys.path.insert(0, str(ROOT / "scripts"))

from app.core.settings import Settings  # noqa: E402
from app.video.ffmpeg_io import FFmpegNotFound, find_binary  # noqa: E402

try:
    find_binary("ffmpeg")
    find_binary("ffprobe")
    HAS_FFMPEG = True
except FFmpegNotFound:
    HAS_FFMPEG = False

needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="FFmpeg introuvable (voir README)")


def _corpus(profile: str) -> tuple[Path, dict]:
    import generate_demo_videos as gen

    out = ROOT / "data" / profile
    gt = gen.generate(profile, out)
    return out, gt


@pytest.fixture(scope="session")
def mini_corpus() -> tuple[Path, dict]:
    if not HAS_FFMPEG:
        pytest.skip("FFmpeg requis")
    return _corpus("mini")


@pytest.fixture(scope="session")
def demo_corpus() -> tuple[Path, dict]:
    if not HAS_FFMPEG:
        pytest.skip("FFmpeg requis")
    return _corpus("demo")


def make_settings(base: Path, **overrides) -> Settings:
    base.mkdir(parents=True, exist_ok=True)
    s = Settings(
        _env_file=None,
        database_url=f"sqlite:///{(base / 'db.sqlite').as_posix()}",
        media_root=base / "media",
        temp_root=base / "temp",
        **overrides,
    )
    s.ensure_dirs()
    return s


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path / "data")


@pytest.fixture
def db(settings: Settings):
    """Base SQLite isolée + thread écrivain."""
    from app.db.session import init_db, make_engine, make_session_factory
    from app.db.writer import DbWriter

    engine = make_engine(settings.database_path)
    init_db(engine)
    sf = make_session_factory(engine)
    writer = DbWriter(sf)
    writer.start()
    yield sf, writer
    writer.close()
    engine.dispose()


def import_file(path: Path, settings: Settings, writer, title: str | None = None):  # noqa: ANN001, ANN201
    from app.services import library

    staged = library.stage_local_file(path, settings)
    movie, created = writer.run_sync(lambda s: library.register_staged(s, staged, settings, title, None))
    if not created:
        staged.temp_path.unlink(missing_ok=True)
    return movie


def load_gt(corpus_dir: Path) -> dict:
    return json.loads((corpus_dir / "ground_truth.json").read_text(encoding="utf-8"))


@pytest.fixture
def copy_file(tmp_path: Path):
    def _copy(src: Path, name: str | None = None) -> Path:
        dst = tmp_path / (name or src.name)
        shutil.copyfile(src, dst)
        return dst
    return _copy
