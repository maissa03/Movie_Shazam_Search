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
    first = -(-start_ms // interval_ms) * interval_ms
    return list(range(first, end_ms, interval_ms))


def format_hms(ms: int) -> str:
    total_s = max(ms, 0) / 1000
    h = int(total_s // 3600)
    m = int((total_s % 3600) // 60)
    s = total_s % 60
    return f"{h:02d}:{m:02d}:{int(s):02d}"
