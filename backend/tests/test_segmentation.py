"""(4) Cohérence des horodatages globaux, segmentation et chevauchement — sur de vraies vidéos."""

from __future__ import annotations

import subprocess

import numpy as np
import pytest
from conftest import needs_ffmpeg

from app.video.ffmpeg_io import find_binary, iter_gray_frames, probe_video
from app.video.pipeline import SegmentTask, analyze_segment
from app.video.segmentation import (
    choose_segment_ms,
    format_hms,
    frame_interval_ms,
    plan_segments,
    segment_timestamps,
)


@pytest.mark.parametrize("duration", [1, 499, 500, 10_000, 36_000, 7_200_123])
def test_plan_covers_without_gap_or_overlap(duration):
    seg = choose_segment_ms(duration, 2.0, 180, 10, 8)
    plans = plan_segments(duration, seg)
    assert plans[0].start_ms == 0 and plans[-1].end_ms == duration
    for a, b in zip(plans, plans[1:]):
        assert a.end_ms == b.start_ms  # ni trou ni recouvrement
    assert all(p.start_ms % 500 == 0 for p in plans)  # bornes sur la grille d'échantillonnage


def test_segment_length_policy():
    assert choose_segment_ms(7_200_000, 2.0, 180, 10, 8) == 180_000  # film de 2 h : 180 s
    assert choose_segment_ms(120_000, 2.0, 180, 10, 8) == 15_000  # film court : >= 8 segments
    assert choose_segment_ms(20_000, 2.0, 180, 10, 8) == 10_000  # minimum 10 s
    with pytest.raises(ValueError):
        frame_interval_ms(3.0)  # 333,33 ms : intervalle non entier refusé


def test_timestamps_grid_and_ownership():
    ts = [t for p in plan_segments(36_000, 10_000) for t in segment_timestamps(p.start_ms, p.end_ms, 500)]
    assert ts == list(range(0, 36_000, 500))  # chaque instant appartient à exactement un segment


def test_format_hms():
    assert format_hms(0) == "00:00:00"
    assert format_hms(2_533_300) == "00:42:13"
    assert format_hms(3_600_000 + 61_000) == "01:01:01"


@needs_ffmpeg
def test_segmented_decode_equals_whole_decode(mini_corpus):
    corpus, gt = mini_corpus
    path = corpus / gt["movies"][0]["file"]
    info = probe_video(path)
    whole = analyze_segment(SegmentTask(str(path), 0, 0, info.duration_ms, 2.0))
    parts_ts, parts_h = [], []
    for p in plan_segments(info.duration_ms, 10_000):
        r = analyze_segment(SegmentTask(str(path), p.index, p.start_ms, p.end_ms, 2.0))
        parts_ts += r.timestamps
        parts_h += r.hashes
    assert parts_ts == whole.timestamps == list(range(0, info.duration_ms, 500))
    assert parts_h == whole.hashes  # frontières de segments sans effet


@needs_ffmpeg
def test_overlap_is_deduplicated(mini_corpus):
    corpus, gt = mini_corpus
    path = corpus / gt["movies"][0]["file"]
    a = analyze_segment(SegmentTask(str(path), 1, 10_000, 20_000, 2.0, overlap_ms=0))
    b = analyze_segment(SegmentTask(str(path), 1, 10_000, 20_000, 2.0, overlap_ms=2_000))
    assert a.timestamps == b.timestamps == list(range(10_000, 20_000, 500))
    assert a.hashes == b.hashes
    assert b.frames_decoded > a.frames_decoded  # la pré-lecture a bien été décodée puis écartée


@needs_ffmpeg
def test_sampled_frames_are_the_right_instants(tmp_path):
    """Vidéo dont chaque image encode son numéro : vérifie l'instant réellement échantillonné,
    y compris après un ``-ss`` qui ne tombe pas sur une image (bug corrigé : +1 case)."""
    path = tmp_path / "frameidx.mp4"
    proc = subprocess.Popen([find_binary("ffmpeg"), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt",
                             "gray", "-s", "64x64", "-r", "25", "-i", "-", "-c:v", "libx264", "-qp", "0",
                             "-pix_fmt", "yuv444p", str(path)], stdin=subprocess.PIPE)
    for i in range(125):  # image i -> niveau de gris 2i
        proc.stdin.write(bytes([2 * i]) * 4096)
    proc.stdin.close()
    proc.wait()
    errors = []
    for start, fps in ((0, 2.0), (1300, 10.0), (1020, 2.0), (2000, 2.0)):
        step = frame_interval_ms(fps)
        for k, frame in iter_gray_frames(path, start_ms=start, duration_ms=2000, fps=fps):
            source_ms = int(round(float(np.median(frame)) / 2)) * 40
            errors.append(source_ms - (start + k * step))
    # Erreur bornée à ~2 images source (arrondis de base de temps internes à FFmpeg), jamais une
    # case entière (500 ms) ni le biais systématique de +240 ms de « round=near ».
    assert max(abs(e) for e in errors) <= 80, errors
    assert abs(sum(errors) / len(errors)) <= 30, errors


@needs_ffmpeg
def test_filename_with_shell_characters_is_safe(mini_corpus, copy_file):
    corpus, gt = mini_corpus
    weird = copy_file(corpus / gt["movies"][0]["file"], "film ; echo PWNED & $(whoami) 'x'.mp4")
    info = probe_video(weird)  # liste d'arguments : le nom n'est jamais interprété par un shell
    assert info.duration_ms > 0
