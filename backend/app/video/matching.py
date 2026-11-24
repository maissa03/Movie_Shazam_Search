from __future__ import annotations

import bisect
import math
import statistics
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field

import numpy as np

from app.video.fingerprint import hamming_many


@dataclass(frozen=True)
class QueryFrame:
    index: int
    ts_ms: int
    hash: int
    informativeness: float


@dataclass(frozen=True)
class CandidateMatch:
    query_index: int
    movie_id: int
    movie_ts_ms: int
    distance: int


@dataclass
class MatchParams:

    max_hamming: int = 12
    min_informativeness: float = 3.0
    probe_radius: int = 1
    max_candidates_per_frame: int = 300
    offset_bucket_ms: int = 500
    offset_tolerance_ms: int = 300
    local_search_ms: int = 500
    hypotheses_per_movie: int = 3
    max_hypotheses: int = 10
    min_informative_frames: int = 4
    accept_score: float = 0.50
    min_coverage: float = 0.5
    min_span_ratio: float = 0.5
    min_matched_frames: int = 4
    ambiguity_ratio: float = 0.85
    distinct_offset_ms: int = 3000

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AlignedFrame:
    query_ts_ms: int
    movie_ts_ms: int
    distance: int


@dataclass
class Hypothesis:
    movie_id: int
    start_ms: int
    end_ms: int
    score: float
    base_similarity: float
    stability: float
    coverage: float
    span_ratio: float
    matched_frames: int
    expected_frames: int
    mean_distance: float
    offset_std_ms: float
    order_rejected: int
    votes: float
    aligned: list[AlignedFrame] = field(default_factory=list)

    def to_dict(self, with_frames: bool = True) -> dict:
        d = asdict(self)
        if not with_frames:
            d.pop("aligned")
        return d


@dataclass
class Decision:
    status: str
    reason: str
    best: Hypothesis | None
    candidates: list[Hypothesis]
    margin: float | None
    informative_frames: int
    total_frames: int

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "reason": self.reason,
            "best": self.best.to_dict() if self.best else None,
            "candidates": [c.to_dict() for c in self.candidates],
            "margin": self.margin,
            "informative_frames": self.informative_frames,
            "total_frames": self.total_frames,
        }


class InMemoryIndex:

    def __init__(self) -> None:
        self._parts: list[tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]] = []
        self._frozen: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray] | None = None

    def add(self, movie_id: int, timestamps: Sequence[int], hashes: Sequence[int],
            informativeness: Sequence[float]) -> None:
        self._parts.append((
            np.full(len(hashes), movie_id, dtype=np.int64),
            np.asarray(timestamps, dtype=np.int64),
            np.array([int(h) for h in hashes], dtype=np.uint64),
            np.asarray(informativeness, dtype=np.float64),
        ))
        self._frozen = None

    def _arrays(self) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        if self._frozen is None:
            if not self._parts:
                self._frozen = (np.zeros(0, np.int64), np.zeros(0, np.int64),
                                np.zeros(0, np.uint64), np.zeros(0))
            else:
                self._frozen = tuple(np.concatenate(cols) for cols in zip(*self._parts))  # type: ignore
        return self._frozen

    def __len__(self) -> int:
        return int(self._arrays()[2].size)

    def find_candidates(self, frames: Sequence[QueryFrame], params: MatchParams) -> list[CandidateMatch]:
        movie, ts, hashes, info = self._arrays()
        usable = info >= params.min_informativeness
        out: list[CandidateMatch] = []
        for q in frames:
            if q.informativeness < params.min_informativeness or hashes.size == 0:
                continue
            dist = hamming_many(hashes, q.hash)
            idx = np.nonzero((dist <= params.max_hamming) & usable)[0]
            out.extend(cap_candidates(q.index, idx, movie, ts, dist, params.max_candidates_per_frame))
        return out


def cap_candidates(qi: int, idx: np.ndarray, movie: np.ndarray, ts: np.ndarray, dist: np.ndarray,
                   cap: int) -> list[CandidateMatch]:
    if idx.size > cap:
        idx = idx[np.lexsort((ts[idx], movie[idx], dist[idx]))[:cap]]
    return [CandidateMatch(qi, int(movie[i]), int(ts[i]), int(dist[i])) for i in idx]


def longest_ordered(pairs: list[AlignedFrame]) -> list[AlignedFrame]:
    if not pairs:
        return []
    tails: list[int] = []
    tails_idx: list[int] = []
    prev = [-1] * len(pairs)
    for i, p in enumerate(pairs):
        pos = bisect.bisect_right(tails, p.query_ts_ms)
        if pos == len(tails):
            tails.append(p.query_ts_ms)
            tails_idx.append(i)
        else:
            tails[pos] = p.query_ts_ms
            tails_idx[pos] = i
        prev[i] = tails_idx[pos - 1] if pos > 0 else -1
    out = []
    k = tails_idx[-1]
    while k != -1:
        out.append(pairs[k])
        k = prev[k]
    return out[::-1]


class _QueryTimeline:

    def __init__(self, frames: Sequence[QueryFrame], step_ms: int, min_info: float):
        ordered = sorted(frames, key=lambda f: f.ts_ms)
        self.step = step_ms
        self.ts = [f.ts_ms for f in ordered]
        self.informative = [f.informativeness >= min_info for f in ordered]
        self.span_ms = (self.ts[-1] + step_ms) if self.ts else 0

    def nearest_informative(self, t_ms: float) -> bool:
        if not self.ts or t_ms < -self.step / 2 or t_ms >= self.span_ms:
            return False
        j = min(max(int(round(t_ms / self.step)), 0), len(self.ts) - 1)
        return self.informative[j]


def _score_offset(offset: int, cands: dict[int, list[tuple[int, int]]], qt: _QueryTimeline,
                  grid_ms: int, params: MatchParams) -> tuple[float, dict]:
    tol = params.offset_tolerance_ms
    first_g = max(0, math.ceil(offset / grid_ms) * grid_ms)
    pairs: list[AlignedFrame] = []
    expected: list[int] = []
    for g in range(first_g, offset + qt.span_ms, grid_ms):
        target = g - offset
        if not qt.nearest_informative(target):
            continue
        expected.append(g)
        best: tuple[int, float, int] | None = None
        for q_ts, d in cands.get(g, ()):
            dev = abs(q_ts - target)
            if dev <= tol and (best is None or (d, dev) < (best[0], best[1])):
                best = (d, dev, q_ts)
        if best is not None:
            pairs.append(AlignedFrame(best[2], g, best[0]))
    ordered = longest_ordered(pairs)
    n_exp = len(expected)
    m = len(ordered)
    if m == 0 or n_exp == 0:
        return 0.0, {"ordered": [], "rejected": len(pairs), "expected": n_exp}
    sims = [1.0 - p.distance / (params.max_hamming + 1) for p in ordered]
    offsets = [p.movie_ts_ms - p.query_ts_ms for p in ordered]
    std = float(statistics.pstdev(offsets)) if m > 1 else 0.0
    base = sum(sims) / n_exp
    stab = 1.0 / (1.0 + (std / tol) ** 2)
    span = (ordered[-1].movie_ts_ms - ordered[0].movie_ts_ms + grid_ms) / (expected[-1] - expected[0] + grid_ms)
    info = {"ordered": ordered, "rejected": len(pairs) - m, "expected": n_exp, "base": base, "stab": stab,
            "std": std, "span": min(span, 1.0), "mean_d": float(statistics.fmean(p.distance for p in ordered)),
            "offsets": offsets}
    return base * stab, info


def _evaluate(movie_id: int, center: int, cands: dict[int, list[tuple[int, int]]], qt: _QueryTimeline,
              query_duration_ms: int, grid_ms: int, votes: float, params: MatchParams) -> Hypothesis | None:
    best_score, best_info = -1.0, None
    for delta in range(-params.local_search_ms, params.local_search_ms + 1, qt.step):
        s, info = _score_offset(center + delta, cands, qt, grid_ms, params)
        if s > best_score + 1e-12:
            best_score, best_info = s, info
    if best_info is None or not best_info["ordered"]:
        return None
    start = max(0, int(statistics.median(best_info["offsets"])))
    i = best_info
    return Hypothesis(
        movie_id=movie_id, start_ms=start, end_ms=start + query_duration_ms,
        score=round(best_score, 4), base_similarity=round(i["base"], 4), stability=round(i["stab"], 4),
        coverage=round(len(i["ordered"]) / i["expected"], 4), span_ratio=round(i["span"], 4),
        matched_frames=len(i["ordered"]), expected_frames=i["expected"], mean_distance=round(i["mean_d"], 3),
        offset_std_ms=round(i["std"], 1), order_rejected=i["rejected"], votes=round(votes, 3),
        aligned=i["ordered"],
    )


def rank_hypotheses(frames: Sequence[QueryFrame], candidates: Sequence[CandidateMatch], query_duration_ms: int,
                    grid_ms: int, query_step_ms: int, params: MatchParams) -> list[Hypothesis]:
    qt = _QueryTimeline(frames, query_step_ms, params.min_informativeness)
    informative = {f.index for f in frames if f.informativeness >= params.min_informativeness}
    qts = {f.index: f.ts_ms for f in frames}

    by_query: dict[int, list[CandidateMatch]] = defaultdict(list)
    by_movie_ts: dict[int, dict[int, list[tuple[int, int]]]] = defaultdict(lambda: defaultdict(list))
    for c in candidates:
        if c.query_index in informative:
            by_query[c.query_index].append(c)
            by_movie_ts[c.movie_id][c.movie_ts_ms].append((qts[c.query_index], c.distance))

    bucket = params.offset_bucket_ms
    votes: dict[tuple[int, int], float] = defaultdict(float)
    for qi, cands in by_query.items():
        per_movie: dict[int, int] = defaultdict(int)
        for c in cands:
            per_movie[c.movie_id] += 1
        for c in cands:
            sim = 1.0 - c.distance / (params.max_hamming + 1)
            b = round((c.movie_ts_ms - qts[qi]) / bucket)
            votes[(c.movie_id, b)] += sim / per_movie[c.movie_id]

    by_movie: dict[int, dict[int, float]] = defaultdict(dict)
    for (m, b), v in votes.items():
        by_movie[m][b] = v
    peaks: list[tuple[float, int, int]] = []
    sep = max(1, params.distinct_offset_ms // bucket)
    for m, hist in by_movie.items():
        smooth = {b: hist.get(b - 1, 0.0) + hist[b] + hist.get(b + 1, 0.0) for b in hist}
        chosen: list[int] = []
        for b, v in sorted(smooth.items(), key=lambda kv: (-kv[1], kv[0])):
            if all(abs(b - c) >= sep for c in chosen):
                chosen.append(b)
                peaks.append((v, m, b))
            if len(chosen) >= params.hypotheses_per_movie:
                break
    peaks.sort(key=lambda p: (-p[0], p[1], p[2]))
    peaks = peaks[: params.max_hypotheses]

    hyps = []
    for v, m, b in peaks:
        h = _evaluate(m, b * bucket, by_movie_ts[m], qt, query_duration_ms, grid_ms, v, params)
        if h is not None:
            hyps.append(h)
    hyps.sort(key=lambda h: (-h.score, -h.matched_frames, h.movie_id, h.start_ms))
    unique: list[Hypothesis] = []
    for h in hyps:
        if all(not same_placement(h, u, params) for u in unique):
            unique.append(h)
    return unique


def same_placement(a: Hypothesis, b: Hypothesis, params: MatchParams) -> bool:
    return a.movie_id == b.movie_id and abs(a.start_ms - b.start_ms) < params.distinct_offset_ms


def unmet_criteria(h: Hypothesis, params: MatchParams) -> list[str]:
    problems = []
    if h.score < params.accept_score:
        problems.append(f"score {h.score:.2f} < {params.accept_score:.2f}")
    if h.coverage < params.min_coverage:
        problems.append(f"couverture {h.coverage:.0%} < {params.min_coverage:.0%}")
    if h.span_ratio < params.min_span_ratio:
        problems.append(f"étendue temporelle {h.span_ratio:.0%} < {params.min_span_ratio:.0%}")
    if h.matched_frames < params.min_matched_frames:
        problems.append(f"{h.matched_frames} images cohérentes < {params.min_matched_frames}")
    return problems


def decide(frames: Sequence[QueryFrame], hypotheses: list[Hypothesis], params: MatchParams,
           grid_ms: int = 500, query_step_ms: int = 100) -> Decision:
    n_total = len(frames)
    n_inf = sum(1 for f in frames if f.informativeness >= params.min_informativeness)
    n_inf_grid = int(round(n_inf * query_step_ms / grid_ms))
    top = hypotheses[:3]
    if n_inf_grid < params.min_informative_frames:
        return Decision("insufficient",
                        f"Clip trop peu informatif : {n_inf} image(s) exploitable(s) sur {n_total} "
                        "(images presque uniformes, noires ou fixes).", None, top, None, n_inf, n_total)
    if not hypotheses:
        return Decision("no_match", "Aucune correspondance suffisamment fiable : aucune image du clip "
                        "ne ressemble à la bibliothèque indexée.", None, [], None, n_inf, n_total)
    best = hypotheses[0]
    problems = unmet_criteria(best, params)
    if problems:
        return Decision("no_match", "Aucune correspondance suffisamment fiable (meilleur candidat rejeté : "
                        + ", ".join(problems) + ").", None, top, None, n_inf, n_total)
    second = next((h for h in hypotheses[1:] if not same_placement(h, best, params)), None)
    margin = None
    if second is not None and best.score > 0:
        margin = round(1.0 - second.score / best.score, 4)
    if second is not None and second.score >= params.ambiguity_ratio * best.score:
        where = "dans le même film à un autre instant" if second.movie_id == best.movie_id else "dans un autre film"
        return Decision("ambiguous", f"Résultat ambigu : un second placement {where} obtient un score "
                        f"très proche ({second.score:.2f} contre {best.score:.2f}).", None, top, margin,
                        n_inf, n_total)
    return Decision("match", "Correspondance retenue : alignement temporel cohérent sur toute la durée du clip.",
                    best, top, 1.0 if margin is None else margin, n_inf, n_total)


def identify(frames: Sequence[QueryFrame], candidates: Sequence[CandidateMatch], query_duration_ms: int,
             grid_ms: int, query_step_ms: int, params: MatchParams) -> Decision:
    hyps = rank_hypotheses(frames, candidates, query_duration_ms, grid_ms, query_step_ms, params)
    return decide(frames, hyps, params, grid_ms, query_step_ms)
