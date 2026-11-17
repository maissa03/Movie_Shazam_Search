"""Validation complète sur le corpus de démonstration (split *test*, seuils figés sur *validation*).

Indexe réellement les 4 films de démo (1280x720, 345 s) en parallèle puis vérifie chaque clip
du split « test » (variantes recompressées, redimensionnées, FPS modifié, scène sombre,
frontières de segment, passage partagé, scène répétée, films inconnus).
"""

from __future__ import annotations

import pytest
from conftest import import_file, make_settings, needs_ffmpeg

from app.concurrency.process_pool import LocalProcessPool
from app.db.session import init_db, make_engine, make_session_factory
from app.db.writer import DbWriter
from app.services import indexing, library, searching
from app.video.matching import MatchParams

pytestmark = [needs_ffmpeg, pytest.mark.slow]


@pytest.fixture(scope="module")
def demo_index(demo_corpus, tmp_path_factory):
    corpus, gt = demo_corpus
    st = make_settings(tmp_path_factory.mktemp("demo"))
    engine = make_engine(st.database_path)
    init_db(engine)
    sf = make_session_factory(engine)
    writer = DbWriter(sf)
    writer.start()
    keys = {}
    with LocalProcessPool(2) as pool:
        for m in [m for m in gt["movies"] if m["indexed"]]:
            movie = import_file(corpus / m["file"], st, writer, m["title"])
            rep = indexing.index_movie_sync(movie.id, library.movie_path(movie, st), movie.duration_ms, st,
                                            writer, pool)
            assert rep.ok, rep.message
            keys[movie.id] = m["key"]
    yield corpus, gt, st, sf, keys
    writer.close()
    engine.dispose()


@pytest.mark.parametrize("split", ["validation", "test"])
@pytest.mark.parametrize("method", ["bands", "exhaustive"])
def test_split(demo_index, split, method):
    corpus, gt, st, sf, keys = demo_index
    params = MatchParams()
    failures = []
    for clip in [c for c in gt["clips"] if c["split"] == split]:
        fp = searching.fingerprint_clip(corpus / clip["file"], st)
        decision, _, _ = searching.search_clip(sf, fp, st, params, method)
        ok = decision.status == clip["expected"]
        if ok and clip["expected"] == "match":
            ok = keys[decision.best.movie_id] == clip["source_key"] and \
                abs(decision.best.start_ms - clip["start_ms"]) <= gt["tolerance_ms"]
        if ok and clip["expected"] == "ambiguous":
            found = {(keys[c.movie_id], round(c.start_ms / 1000)) for c in decision.candidates[:2]}
            wanted = {(clip["source_key"], round(clip["start_ms"] / 1000))} | {
                (a["source_key"], round(a["start_ms"] / 1000)) for a in clip["alternatives"]}
            ok = found == wanted
        if not ok:
            failures.append((clip["id"], decision.status, decision.reason))
    assert not failures, failures
