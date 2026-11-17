"""Découpage d'un film en segments indépendants et horodatages globaux.

Un *segment* est une **unité de travail CPU** (pas un fichier vidéo) : ``[start_ms, end_ms)``.
Chaque segment est décodé séparément (``-ss start``) par un processus du pool.

Garanties (testées) :
  * les segments couvrent ``[0, durée)`` sans trou ni recouvrement ;
  * toutes les bornes sont des multiples de l'intervalle d'échantillonnage
    (500 ms à 2 images/s), donc les horodatages ``start + k*intervalle`` d'un segment
    tombent sur la **même grille globale** que si le film était décodé d'un seul tenant ;
  * règle de propriété : un horodatage ``t`` appartient au seul segment tel que
    ``start <= t < end``.  Si un chevauchement (``overlap_ms``) est demandé, les images
    de pré-lecture sont décodées puis écartées : il n'y a jamais de doublon.

Choix de la longueur (déviation documentée du « 180 s fixe ») : on vise au moins
``target_min_segments`` segments par film pour que les films courts profitent aussi du
parallélisme, en restant entre ``min_segment_s`` et ``max_segment_s``.  Le découpage ne
dépend **pas** du nombre de processus : séquentiel et parallèle traitent exactement les
mêmes segments.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class SegmentPlan:
    index: int
    start_ms: int
    end_ms: int

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


def frame_interval_ms(fps: float) -> int:
    interval = 1000.0 / fps
    if abs(interval - round(interval)) > 1e-9:
        raise ValueError("FINGERPRINT_FPS doit donner un intervalle entier en ms (1, 2, 4, 5, 8, 10...)")
    return int(round(interval))


def choose_segment_ms(
    duration_ms: int, fps: float, max_segment_s: float, min_segment_s: float, target_min_segments: int
) -> int:
    """Longueur de segment (ms), multiple de la seconde et de l'intervalle d'échantillonnage."""
    interval = frame_interval_ms(fps)
    wanted_s = math.ceil(duration_ms / 1000 / max(target_min_segments, 1))
    seg_s = min(max_segment_s, max(min_segment_s, wanted_s))
    seg_ms = int(round(seg_s * 1000))
    unit = math.lcm(1000, interval)
    return max(unit, (seg_ms // unit) * unit)


def plan_segments(duration_ms: int, segment_ms: int) -> list[SegmentPlan]:
    if duration_ms <= 0:
        return []
    if segment_ms <= 0:
        raise ValueError("segment_ms doit être positif")
    plans = []
    start = 0
    index = 0
    while start < duration_ms:
        end = min(start + segment_ms, duration_ms)
        plans.append(SegmentPlan(index, start, end))
        start = end
        index += 1
    return plans


def segment_timestamps(start_ms: int, end_ms: int, interval_ms: int) -> list[int]:
    """Horodatages globaux attendus pour un segment (grille alignée sur 0)."""
    first = -(-start_ms // interval_ms) * interval_ms  # premier multiple >= start
    return list(range(first, end_ms, interval_ms))


def format_hms(ms: int) -> str:
    """``HH:MM:SS`` (ou ``HH:MM:SS.s`` si besoin de précision)."""
    total_s = max(ms, 0) / 1000
    h = int(total_s // 3600)
    m = int((total_s % 3600) // 60)
    s = total_s % 60
    return f"{h:02d}:{m:02d}:{int(s):02d}"
