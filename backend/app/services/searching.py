from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from app.core.settings import Settings
from app.db import repo
from app.db.search_repo import CandidateStats, find_candidates
from app.video.ffmpeg_io import VideoInfo, find_binary, probe_video
from app.video.matching import Decision, Hypothesis, MatchParams, QueryFrame, identify
from app.video.pipeline import SegmentTask, analyze_segment
from app.video.segmentation import format_hms, frame_interval_ms


class QueryRejected(ValueError):
    pass


@dataclass
class ClipFingerprints:
    info: VideoInfo
    frames: list[QueryFrame]
    step_ms: int
    decode_s: float
    hash_s: float


def query_fps(settings: Settings) -> float:
    return settings.fingerprint_fps * settings.query_oversampling


def validate_clip(path: Path, settings: Settings) -> VideoInfo:
    info = probe_video(path)
    lo, hi = settings.min_query_duration_seconds, settings.max_query_duration_seconds
    if not lo * 1000 <= info.duration_ms <= hi * 1000 + 500:
        raise QueryRejected(f"durée du clip {info.duration_ms / 1000:.1f} s hors de la fenêtre autorisée "
                            f"({lo:g}–{hi:g} s)")
    return info


def fingerprint_clip(path: Path, settings: Settings, info: VideoInfo | None = None) -> ClipFingerprints:
    info = info or validate_clip(path, settings)
    fps = query_fps(settings)
    res = analyze_segment(SegmentTask(str(path), 0, 0, info.duration_ms, fps, ffmpeg_threads=2,
                                      timeout_s=180, ffmpeg_bin=find_binary("ffmpeg")))
    frames = [QueryFrame(i, ts, h, inf)
              for i, (ts, h, inf) in enumerate(zip(res.timestamps, res.hashes, res.informativeness))]
    if not frames:
        raise QueryRejected("aucune image n'a pu être décodée dans ce clip")
    return ClipFingerprints(info, frames, frame_interval_ms(fps), res.decode_s, res.hash_s)


def search_clip(session_factory: sessionmaker[Session], clip: ClipFingerprints, settings: Settings,
                params: MatchParams, method: str = "bands") -> tuple[Decision, CandidateStats, float]:
    with session_factory() as s:
        cands, stats = find_candidates(s, clip.frames, params, settings.index_version, method)
    t0 = time.perf_counter()
    decision = identify(clip.frames, cands, clip.info.duration_ms, frame_interval_ms(settings.fingerprint_fps),
                        clip.step_ms, params)
    return decision, stats, (time.perf_counter() - t0) * 1000


def _hyp_payload(h: Hypothesis, titles: dict[int, dict]) -> dict:
    meta = titles.get(h.movie_id, {})
    d = h.to_dict(with_frames=False)
    d.update({
        "title": meta.get("title", f"film #{h.movie_id} (supprimé)"), "year": meta.get("year"),
        "movie_duration_ms": meta.get("duration_ms"), "has_thumbnail": meta.get("has_thumbnail", False),
        "start_hms": format_hms(h.start_ms), "end_hms": format_hms(h.end_ms),
        "aligned": [[a.query_ts_ms, a.movie_ts_ms, a.distance] for a in h.aligned],
    })
    return d


def decision_payload(decision: Decision, session_factory: sessionmaker[Session]) -> dict:
    ids = {c.movie_id for c in decision.candidates} | ({decision.best.movie_id} if decision.best else set())
    titles: dict[int, dict] = {}
    with session_factory() as s:
        for mid in ids:
            m = repo.get_movie(s, mid)
            if m is not None:
                titles[mid] = {"title": m.title, "year": m.year, "duration_ms": m.duration_ms,
                               "has_thumbnail": bool(m.thumbnail_name)}
    return {
        "status": decision.status,
        "reason": decision.reason,
        "margin": decision.margin,
        "informative_frames": decision.informative_frames,
        "total_frames": decision.total_frames,
        "best": _hyp_payload(decision.best, titles) if decision.best else None,
        "candidates": [_hyp_payload(c, titles) for c in decision.candidates],
    }
