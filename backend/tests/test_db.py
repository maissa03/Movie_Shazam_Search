"""(7) Pas de doublons d'empreintes, pas de mélange de versions ; intégrité de l'index."""

from __future__ import annotations

import pytest
from sqlalchemy import func, insert, select
from sqlalchemy.exc import IntegrityError

from app.db import repo
from app.db.models import Fingerprint, FingerprintBand, Segment
from app.video.fingerprint import to_blob
from app.video.pipeline import SegmentResult
from app.video.segmentation import plan_segments


def _movie(s, n: int = 1):  # noqa: ANN001, ANN202
    return repo.create_movie(s, title=f"m{n}", year=None, original_filename="x", stored_name=f"x{n}.mp4",
                             file_size=1, file_mtime_ns=1, content_hash=f"h{n}", duration_ms=4000, width=1,
                             height=1, codec="h264", fps=25, format_name="mp4")


def _result(start: int, end: int, base: int = 0) -> SegmentResult:
    ts = list(range(start, end, 500))
    return SegmentResult(0, start, end, timestamps=ts, hashes=[base + i * 7919 for i in range(len(ts))],
                         informativeness=[30.0] * len(ts))


def _count(s, model, **where) -> int:  # noqa: ANN001
    stmt = select(func.count()).select_from(model)
    for k, v in where.items():
        stmt = stmt.where(getattr(model, k) == v)
    return s.scalar(stmt)


def test_unique_constraint_blocks_duplicates(db):
    sf, writer = db

    def go(s):  # noqa: ANN001, ANN202
        m = _movie(s)
        seg = repo.prepare_index(s, m.id, "v1", 4000, plan_segments(4000, 4000))[0]
        row = {"movie_id": m.id, "segment_id": seg.segment_id, "timestamp_ms": 0, "hash_blob": to_blob(1),
               "informativeness": 1.0, "algorithm_version": "v1"}
        s.execute(insert(Fingerprint), [row])
        s.execute(insert(Fingerprint), [row])

    with pytest.raises(IntegrityError):
        writer.run_sync(go)
    with sf() as s:
        assert _count(s, Fingerprint) == 0  # transaction annulée en entier


def test_save_segment_is_idempotent(db):
    sf, writer = db

    def go(s):  # noqa: ANN001, ANN202
        m = _movie(s)
        seg = repo.prepare_index(s, m.id, "v1", 4000, plan_segments(4000, 4000))[0]
        for _ in range(3):  # ré-exécutions (reprise, double livraison...)
            repo.save_segment_result(s, m.id, seg.segment_id, "v1", _result(0, 4000))
        return m.id

    mid = writer.run_sync(go)
    with sf() as s:
        assert _count(s, Fingerprint, movie_id=mid) == 8
        assert _count(s, FingerprintBand) == 8 * 4
        assert repo.get_movie(s, mid).fingerprints_count == 8


def test_new_version_never_mixes_with_old(db):
    sf, writer = db

    def index(s, mid, version):  # noqa: ANN001, ANN202
        todo = repo.prepare_index(s, mid, version, 2000, plan_segments(4000, 2000))
        for t in todo:
            repo.save_segment_result(s, mid, t.segment_id, version, _result(t.start_ms, t.end_ms, base=len(version)))
        return repo.finalize_index(s, mid, version)

    mid = writer.run_sync(lambda s: _movie(s).id)
    assert writer.run_sync(lambda s: index(s, mid, "v1"))[0]
    assert writer.run_sync(lambda s: index(s, mid, "v2-longer"))[0]
    with sf() as s:
        versions = set(s.scalars(select(Fingerprint.algorithm_version).where(Fingerprint.movie_id == mid)))
        assert versions == {"v2-longer"}
        assert {x.index_version for x in repo.movie_segments(s, mid)} == {"v2-longer"}


def test_incremental_prepare_reuses_done_segments(db):
    sf, writer = db
    mid = writer.run_sync(lambda s: _movie(s).id)
    plans = plan_segments(4000, 1000)
    todo = writer.run_sync(lambda s: repo.prepare_index(s, mid, "v1", 1000, plans))
    assert len(todo) == 4
    for t in todo[:2]:  # 2 segments terminés, puis « annulation »
        writer.run_sync(lambda s, t=t: repo.save_segment_result(s, mid, t.segment_id, "v1",
                                                                 _result(t.start_ms, t.end_ms)))
    todo2 = writer.run_sync(lambda s: repo.prepare_index(s, mid, "v1", 1000, plans))
    assert [t.start_ms for t in todo2] == [2000, 3000]  # seuls les segments manquants
    todo3 = writer.run_sync(lambda s: repo.prepare_index(s, mid, "v1", 1000, plans, force=True))
    assert len(todo3) == 4  # force : tout recalculer


def test_finalize_refuses_incomplete_index(db):
    sf, writer = db
    mid = writer.run_sync(lambda s: _movie(s).id)
    todo = writer.run_sync(lambda s: repo.prepare_index(s, mid, "v1", 2000, plan_segments(4000, 2000)))
    writer.run_sync(lambda s: repo.save_segment_result(s, mid, todo[0].segment_id, "v1", _result(0, 2000)))
    ok, msg = writer.run_sync(lambda s: repo.finalize_index(s, mid, "v1"))
    assert not ok and "incomplet" in msg
    with sf() as s:
        assert repo.get_movie(s, mid).status == "failed"  # jamais « indexed » avec un index partiel


def test_delete_cascades_everything(db):
    sf, writer = db

    def go(s):  # noqa: ANN001, ANN202
        m = _movie(s)
        t = repo.prepare_index(s, m.id, "v1", 4000, plan_segments(4000, 4000))[0]
        repo.save_segment_result(s, m.id, t.segment_id, "v1", _result(0, 4000))
        return m.id

    mid = writer.run_sync(go)
    writer.run_sync(lambda s: repo.delete_movie(s, mid))
    with sf() as s:
        assert _count(s, Fingerprint) == 0 and _count(s, FingerprintBand) == 0 and _count(s, Segment) == 0


def test_hash_values_above_2_63_survive_storage(db):
    sf, writer = db
    big = [2**64 - 1, 2**63, 2**63 + 12345, 0]

    def go(s):  # noqa: ANN001, ANN202
        m = _movie(s)
        t = repo.prepare_index(s, m.id, "v1", 2000, plan_segments(2000, 2000))[0]
        r = SegmentResult(0, 0, 2000, timestamps=[0, 500, 1000, 1500], hashes=big, informativeness=[9.0] * 4)
        repo.save_segment_result(s, m.id, t.segment_id, "v1", r)
        return m.id

    mid = writer.run_sync(go)
    with sf() as s:
        from app.video.fingerprint import from_blob
        assert [from_blob(b) for _, b, _ in repo.movie_fingerprints(s, mid, "v1")] == big
