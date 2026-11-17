"""(5) Alignement temporel ; (6) rejet d'un clip inconnu ou trop ambigu — logique pure, sans base."""

from __future__ import annotations

import random

from app.video.matching import AlignedFrame, InMemoryIndex, MatchParams, QueryFrame, identify, longest_ordered

GRID, STEP = 500, 100


def _movie(seed: int, n: int) -> list[int]:
    rng = random.Random(seed)
    return [rng.getrandbits(64) for _ in range(n)]


def _flip(v: int, d: int, rng: random.Random) -> int:
    for b in rng.sample(range(64), d):
        v ^= 1 << b
    return v


def _clip_from(movie: list[int], start_ms: int, dur_ms: int, noise: int, seed: int) -> list[QueryFrame]:
    """Simule un clip échantillonné tous les 100 ms : l'image du clip à l'instant q est l'image
    du film la plus proche **avant** start+q (comme FFmpeg), avec quelques bits bruités."""
    rng = random.Random(seed)
    frames = []
    for k in range(dur_ms // STEP):
        t = start_ms + k * STEP
        g = (t // GRID)
        if t % GRID == 0 or (t % GRID) <= 50:  # en phase avec la grille : même image
            h = _flip(movie[g], noise, rng)
        else:  # hors phase : image intermédiaire (inconnue de l'index)
            h = rng.getrandbits(64)
        frames.append(QueryFrame(k, k * STEP, h, 40.0))
    return frames


def _index(movies: dict[int, list[int]]) -> InMemoryIndex:
    idx = InMemoryIndex()
    for mid, hashes in movies.items():
        idx.add(mid, [i * GRID for i in range(len(hashes))], hashes, [40.0] * len(hashes))
    return idx


def _identify(idx: InMemoryIndex, frames: list[QueryFrame], dur: int, params: MatchParams | None = None):
    params = params or MatchParams()
    return identify(frames, idx.find_candidates(frames, params), dur, GRID, STEP, params)


def test_finds_correct_movie_and_offset():
    movies = {1: _movie(1, 600), 2: _movie(2, 600)}
    idx = _index(movies)
    for start in (0, 42_300, 120_000, 287_500):
        frames = _clip_from(movies[2], start, 10_000, noise=3, seed=start)
        d = _identify(idx, frames, 10_000)
        assert d.status == "match", d.reason
        assert d.best.movie_id == 2
        assert abs(d.best.start_ms - start) <= 100  # précision au pas du clip


def test_a_few_similar_frames_are_not_enough():
    """Quelques images ressemblantes à des endroits incohérents ne font PAS un film."""
    movie = _movie(3, 600)
    idx = _index({1: movie})
    rng = random.Random(4)
    frames = [QueryFrame(k, k * STEP, rng.getrandbits(64), 40.0) for k in range(100)]
    for k, pos in ((5, 10), (30, 400), (70, 150), (90, 520)):  # 4 images réelles mais dispersées
        frames[k] = QueryFrame(k, k * STEP, movie[pos], 40.0)
    d = _identify(idx, frames, 10_000)
    assert d.status == "no_match"


def test_shuffled_clip_is_rejected_by_temporal_order():
    movie = _movie(5, 600)
    idx = _index({1: movie})
    good = _clip_from(movie, 50_000, 10_000, noise=0, seed=1)
    rng = random.Random(6)
    order = list(range(len(good)))
    rng.shuffle(order)
    shuffled = [QueryFrame(k, k * STEP, good[j].hash, 40.0) for k, j in enumerate(order)]
    assert _identify(idx, good, 10_000).status == "match"
    assert _identify(idx, shuffled, 10_000).status == "no_match"


def test_unknown_clip_is_rejected():
    idx = _index({1: _movie(7, 600), 2: _movie(8, 600)})
    unknown = _clip_from(_movie(99, 600), 30_000, 10_000, noise=0, seed=2)
    d = _identify(idx, unknown, 10_000)
    assert d.status == "no_match" and d.best is None


def test_shared_passage_is_ambiguous_but_context_resolves_it():
    a, b = _movie(10, 400), _movie(11, 400)
    b[100:116] = a[40:56]  # 8 s identiques : B[50 s..58 s] == A[20 s..28 s]
    idx = _index({1: a, 2: b})
    inside = _clip_from(a, 20_000, 7_000, noise=1, seed=3)
    d = _identify(idx, inside, 7_000)
    assert d.status == "ambiguous"
    assert {c.movie_id for c in d.candidates[:2]} == {1, 2}
    with_context = _clip_from(a, 16_000, 15_000, noise=1, seed=3)  # 4 s propres à A en plus
    d2 = _identify(idx, with_context, 15_000)
    assert d2.status == "match" and d2.best.movie_id == 1


def test_repeated_scene_inside_one_movie_is_ambiguous():
    a = _movie(12, 400)
    a[200:212] = a[60:72]  # flashback : la même scène à 30 s et à 100 s
    d = _identify(_index({1: a}), _clip_from(a, 30_000, 5_000, noise=0, seed=4), 5_000)
    assert d.status == "ambiguous"
    assert sorted(c.start_ms for c in d.candidates[:2]) == [30_000, 100_000]


def test_uninformative_clip_is_reported_as_insufficient():
    idx = _index({1: _movie(13, 100)})
    black = [QueryFrame(k, k * STEP, 0, 0.3) for k in range(80)]
    assert _identify(idx, black, 8_000).status == "insufficient"


def test_longest_ordered_subsequence():
    pairs = [AlignedFrame(q, m, 0) for m, q in [(0, 0), (500, 100), (1000, 900), (1500, 300), (2000, 400)]]
    kept = longest_ordered(pairs)
    assert [p.movie_ts_ms for p in kept] == [0, 500, 1500, 2000]  # 900 est hors ordre
