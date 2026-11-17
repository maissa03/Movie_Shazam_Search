"""(3) Index par bandes : candidat exact retrouvé ; garanties théoriques ; faux négatifs mesurés."""

from __future__ import annotations

import random

from app.db import repo
from app.db.search_repo import band_candidates, exhaustive_candidates
from app.video.matching import MatchParams, QueryFrame
from app.video.pipeline import SegmentResult
from app.video.segmentation import plan_segments

VERSION = "test-v1"


def _fill(db, hashes: list[int]) -> int:
    sf, writer = db

    def setup(s):  # noqa: ANN001, ANN202
        m = repo.create_movie(s, title="synthetic", year=None, original_filename="x", stored_name="x.mp4",
                              file_size=1, file_mtime_ns=1, content_hash="h", duration_ms=len(hashes) * 500,
                              width=1, height=1, codec="h264", fps=25, format_name="mp4")
        todo = repo.prepare_index(s, m.id, VERSION, len(hashes) * 500, plan_segments(len(hashes) * 500,
                                                                                     len(hashes) * 500))
        r = SegmentResult(0, 0, len(hashes) * 500, timestamps=[i * 500 for i in range(len(hashes))],
                          hashes=hashes, informativeness=[50.0] * len(hashes))
        repo.save_segment_result(s, m.id, todo[0].segment_id, VERSION, r)
        repo.finalize_index(s, m.id, VERSION)
        return m.id

    return writer.run_sync(setup)


def _flip(value: int, d: int, rng: random.Random) -> int:
    for b in rng.sample(range(64), d):
        value ^= 1 << b
    return value


def test_exact_candidate_is_found(db):
    rng = random.Random(1)
    hashes = [rng.getrandbits(64) for _ in range(500)]
    _fill(db, hashes)
    sf, _ = db
    frames = [QueryFrame(0, 0, hashes[123], 99.0)]
    with sf() as s:
        cands, stats = band_candidates(s, frames, MatchParams(probe_radius=0), VERSION)
    assert any(c.movie_ts_ms == 123 * 500 and c.distance == 0 for c in cands)
    assert stats.probes == 4 and stats.rows_scanned >= 1  # 4 sondes (une par bande), au moins une ligne


def test_pigeonhole_guarantees_and_measured_false_negatives(db):
    """Sans multi-probe : rappel garanti si d<=3 ; avec multi-probe 1 bit : garanti si d<=7.
    Au-delà, le rappel est mesuré (filtre approximatif assumé)."""
    rng = random.Random(2)
    hashes = [rng.getrandbits(64) for _ in range(400)]
    _fill(db, hashes)
    sf, _ = db
    recall: dict[tuple[int, int], float] = {}
    for radius in (0, 1):
        params = MatchParams(probe_radius=radius, max_hamming=12, max_candidates_per_frame=10_000)
        for d in range(0, 13):
            picks = rng.sample(range(len(hashes)), 60)
            frames = [QueryFrame(i, 0, _flip(hashes[p], d, rng), 99.0) for i, p in enumerate(picks)]
            with sf() as s:
                cands, _ = band_candidates(s, frames, params, VERSION)
            found = {c.query_index for c in cands if c.movie_ts_ms == picks[c.query_index] * 500}
            recall[(radius, d)] = len(found) / len(picks)
    for d in range(0, 4):
        assert recall[(0, d)] == 1.0, f"d={d} doit être garanti sans multi-probe"
    for d in range(0, 8):
        assert recall[(1, d)] == 1.0, f"d={d} doit être garanti avec multi-probe"
    assert recall[(1, 12)] >= recall[(0, 12)]  # le multi-probe améliore le rappel
    assert recall[(0, 12)] < 1.0  # le filtre est bien approximatif : des faux négatifs existent
    print("rappel (rayon, distance) :", {k: round(v, 2) for k, v in recall.items()})


def test_band_and_exhaustive_agree_for_close_matches(db):
    rng = random.Random(3)
    hashes = [rng.getrandbits(64) for _ in range(300)]
    _fill(db, hashes)
    sf, _ = db
    frames = [QueryFrame(i, 0, _flip(hashes[i * 7], i % 6, rng), 99.0) for i in range(40)]
    params = MatchParams(probe_radius=1, max_hamming=7)
    with sf() as s:
        b, _ = band_candidates(s, frames, params, VERSION)
        e, _ = exhaustive_candidates(s, frames, params, VERSION)
    key = lambda c: (c.query_index, c.movie_id, c.movie_ts_ms, c.distance)  # noqa: E731
    assert sorted(map(key, b)) == sorted(map(key, e))  # d <= 7 : résultats identiques
