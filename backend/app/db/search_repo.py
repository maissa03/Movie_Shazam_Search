"""Recherche de candidats dans SQLite : index par bandes (production) ou exhaustive (référence).

Index par bandes (filtre approximatif, honnête sur ses limites) :
  chaque pHash 64 bits = 4 bandes de 16 bits.  Pour une image du clip on récupère les
  empreintes qui partagent **au moins une bande** (valeur exacte, et en *multi-probe*
  aussi les 16 valeurs à 1 bit près), puis on calcule leur vraie distance de Hamming.
  * sans multi-probe : rappel garanti pour d <= 3 (principe des tiroirs : 3 bits changés
    ne peuvent pas toucher les 4 bandes) ;
  * avec multi-probe 1 bit : rappel garanti pour d <= 7 ; au-delà (8..12) le rappel est
    seulement probable -> il est **mesuré** contre la recherche exhaustive (benchmarks).

Recherche exhaustive : lit toutes les empreintes indexées par blocs (mémoire bornée) et
calcule les distances avec NumPy.  Exacte, mais coût proportionnel à la taille de la base.
"""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Fingerprint, Movie
from app.video.fingerprint import band_probes, bands, blobs_to_array, hamming_many
from app.video.matching import CandidateMatch, MatchParams, QueryFrame

_CHUNK = 900  # nombre de paramètres par requête IN (...)


@dataclass
class CandidateStats:
    method: str
    query_frames: int = 0
    probes: int = 0
    rows_scanned: int = 0
    unique_fingerprints: int = 0
    candidates: int = 0
    elapsed_ms: float = 0.0
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"method": self.method, "query_frames": self.query_frames, "probes": self.probes,
                "rows_scanned": self.rows_scanned, "unique_fingerprints": self.unique_fingerprints,
                "candidates": self.candidates, "elapsed_ms": round(self.elapsed_ms, 2), **self.extra}


def _chunks(seq: list, n: int = _CHUNK):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def band_candidates(s: Session, frames: Sequence[QueryFrame], params: MatchParams,
                    version: str) -> tuple[list[CandidateMatch], CandidateStats]:
    """Sondes -> table TEMP -> **une seule jointure exécutée par SQLite** -> distances NumPy.

    La table ``TEMP`` est propre à la connexion (base temporaire séparée) : elle n'écrit pas
    dans le fichier principal et ne concurrence donc pas le thread écrivain.  ``CROSS JOIN``
    impose l'ordre de jointure à SQLite : on parcourt les sondes, et pour chacune on descend
    dans le B-tree ``(band_index, band_value)`` de ``fingerprint_bands``.
    """
    t0 = time.perf_counter()
    stats = CandidateStats("bands")
    usable = [q for q in frames if q.informativeness >= params.min_informativeness]
    stats.query_frames = len(usable)
    probe_rows = {(i, pv, q.index) for q in usable for i, bv in enumerate(bands(q.hash))
                  for pv in band_probes(bv, params.probe_radius)}
    stats.probes = len(probe_rows)
    if not probe_rows:
        stats.elapsed_ms = (time.perf_counter() - t0) * 1000
        return [], stats
    conn = s.connection()
    conn.exec_driver_sql("CREATE TEMP TABLE IF NOT EXISTS ms_probe "
                         "(band_index INTEGER NOT NULL, band_value INTEGER NOT NULL, qi INTEGER NOT NULL)")
    conn.exec_driver_sql("DELETE FROM ms_probe")
    conn.exec_driver_sql("INSERT INTO ms_probe (band_index, band_value, qi) VALUES (?, ?, ?)", list(probe_rows))
    rows = conn.exec_driver_sql(
        "SELECT DISTINCT p.qi, f.movie_id, f.timestamp_ms, f.hash_blob "
        "FROM ms_probe AS p "
        "CROSS JOIN fingerprint_bands AS b ON b.band_index = p.band_index AND b.band_value = p.band_value "
        "JOIN fingerprints AS f ON f.id = b.fingerprint_id "
        "JOIN movies AS m ON m.id = f.movie_id "
        "WHERE m.status = 'indexed' AND f.algorithm_version = ? AND f.informativeness >= ?",
        (version, params.min_informativeness)).fetchall()
    conn.exec_driver_sql("DELETE FROM ms_probe")
    stats.rows_scanned = len(rows)
    if not rows:
        stats.elapsed_ms = (time.perf_counter() - t0) * 1000
        return [], stats

    n = len(rows)
    qi = np.fromiter((r[0] for r in rows), dtype=np.int64, count=n)
    movie = np.fromiter((r[1] for r in rows), dtype=np.int64, count=n)
    ts_arr = np.fromiter((r[2] for r in rows), dtype=np.int64, count=n)
    hashes = blobs_to_array([r[3] for r in rows])
    stats.unique_fingerprints = len(set(zip(movie.tolist(), ts_arr.tolist())))
    query_hash = np.zeros(max(q.index for q in usable) + 1, dtype=np.uint64)
    for q in usable:
        query_hash[q.index] = np.uint64(q.hash)
    dist = np.bitwise_count(np.bitwise_xor(hashes, query_hash[qi])).astype(np.int16)  # vraies distances
    keep = dist <= params.max_hamming
    qi, movie, ts_arr, dist = qi[keep], movie[keep], ts_arr[keep], dist[keep]
    # tri (image du clip, distance, film, instant) puis plafond par image : mêmes règles que l'exhaustif
    order = np.lexsort((ts_arr, movie, dist, qi))
    qi, movie, ts_arr, dist = qi[order], movie[order], ts_arr[order], dist[order]
    if qi.size:
        group_start = np.r_[0, np.flatnonzero(np.diff(qi)) + 1]
        rank = np.arange(qi.size) - np.repeat(group_start, np.diff(np.r_[group_start, qi.size]))
        sel = rank < params.max_candidates_per_frame
        qi, movie, ts_arr, dist = qi[sel], movie[sel], ts_arr[sel], dist[sel]
    out = [CandidateMatch(int(a), int(b), int(c), int(d))
           for a, b, c, d in zip(qi.tolist(), movie.tolist(), ts_arr.tolist(), dist.tolist())]
    stats.candidates = len(out)
    stats.elapsed_ms = (time.perf_counter() - t0) * 1000
    return out, stats


def exhaustive_candidates(s: Session, frames: Sequence[QueryFrame], params: MatchParams, version: str,
                          chunk_rows: int = 50_000) -> tuple[list[CandidateMatch], CandidateStats]:
    t0 = time.perf_counter()
    stats = CandidateStats("exhaustive")
    usable = [q for q in frames if q.informativeness >= params.min_informativeness]
    stats.query_frames = len(usable)
    found: dict[int, list[tuple[int, int, int]]] = defaultdict(list)  # qi -> (dist, movie, ts)
    stmt = (select(Fingerprint.movie_id, Fingerprint.timestamp_ms, Fingerprint.hash_blob)
            .join(Movie, Movie.id == Fingerprint.movie_id)
            .where(Movie.status == "indexed", Fingerprint.algorithm_version == version,
                   Fingerprint.informativeness >= params.min_informativeness)
            .execution_options(yield_per=chunk_rows))
    result = s.execute(stmt)
    for part in result.partitions(chunk_rows):
        movie = np.fromiter((r[0] for r in part), dtype=np.int64, count=len(part))
        ts = np.fromiter((r[1] for r in part), dtype=np.int64, count=len(part))
        hashes = blobs_to_array([r[2] for r in part])
        stats.rows_scanned += len(part)
        for q in usable:
            dist = hamming_many(hashes, q.hash)
            for i in np.nonzero(dist <= params.max_hamming)[0]:
                found[q.index].append((int(dist[i]), int(movie[i]), int(ts[i])))
    out: list[CandidateMatch] = []
    for qi, lst in found.items():
        lst.sort()
        out.extend(CandidateMatch(qi, m, t, d) for d, m, t in lst[: params.max_candidates_per_frame])
    stats.candidates = len(out)
    stats.unique_fingerprints = len({(c.movie_id, c.movie_ts_ms) for c in out})
    stats.elapsed_ms = (time.perf_counter() - t0) * 1000
    return out, stats


def find_candidates(s: Session, frames: Sequence[QueryFrame], params: MatchParams, version: str,
                    method: str = "bands") -> tuple[list[CandidateMatch], CandidateStats]:
    if method == "exhaustive":
        return exhaustive_candidates(s, frames, params, version)
    return band_candidates(s, frames, params, version)
