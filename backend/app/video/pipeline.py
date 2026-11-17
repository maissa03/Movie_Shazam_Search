"""Fonction métier unique d'analyse d'un segment (utilisée en séquentiel ET en parallèle).

``analyze_segment`` est définie **au niveau du module** et ne reçoit que des objets
simples sérialisables (chemin validé, bornes, paramètres) : elle peut donc être envoyée
à un processus du ``ProcessPoolExecutor`` sous Windows (démarrage ``spawn``).

Elle retourne un résultat **compact** : quelques centaines d'entiers (horodatages,
empreintes 64 bits, informativité), jamais d'images brutes.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

from app.video.ffmpeg_io import FRAME_SIZE, DecodeStats, FFmpegError, iter_gray_frames
from app.video.fingerprint import phash_gray
from app.video.segmentation import frame_interval_ms

# Positionnés dans chaque processus du pool par ``worker_initializer`` (héritage au lancement).
_ABORT_EVENT = None  # multiprocessing.Event : arrêt de l'application -> abandon du segment en cours
_BARRIER = None  # multiprocessing.Barrier : préchauffage, prouve N processus distincts


class SegmentAborted(RuntimeError):
    """Le segment a été abandonné car l'application s'arrête."""


@dataclass(frozen=True)
class SegmentTask:
    """Description sérialisable d'une unité de travail."""

    video_path: str
    segment_index: int
    start_ms: int
    end_ms: int
    fps: float
    overlap_ms: int = 0
    frame_size: int = FRAME_SIZE
    ffmpeg_threads: int = 1
    timeout_s: float = 600.0
    ffmpeg_bin: str | None = None
    movie_id: int | None = None
    job_id: str | None = None


@dataclass
class SegmentResult:
    segment_index: int
    start_ms: int
    end_ms: int
    timestamps: list[int] = field(default_factory=list)
    hashes: list[int] = field(default_factory=list)
    informativeness: list[float] = field(default_factory=list)
    frames_decoded: int = 0
    decode_s: float = 0.0
    hash_s: float = 0.0
    wall_s: float = 0.0
    pid: int = 0

    @property
    def count(self) -> int:
        return len(self.hashes)


def analyze_segment(task: SegmentTask) -> SegmentResult:
    """Décode le segment en flux et calcule un pHash par image échantillonnée."""
    t_start = time.perf_counter()
    interval = frame_interval_ms(task.fps)
    overlap = (task.overlap_ms // interval) * interval  # pré-lecture alignée sur la grille
    decode_start = max(0, task.start_ms - overlap)
    stats = DecodeStats()
    result = SegmentResult(task.segment_index, task.start_ms, task.end_ms, pid=os.getpid())
    hash_s = 0.0
    for k, frame in iter_gray_frames(
        task.video_path,
        start_ms=decode_start,
        duration_ms=task.end_ms - decode_start,
        fps=task.fps,
        size=task.frame_size,
        threads=task.ffmpeg_threads,
        timeout_s=task.timeout_s,
        stats=stats,
        ffmpeg_bin=task.ffmpeg_bin,
    ):
        if _ABORT_EVENT is not None and k % 16 == 0 and _ABORT_EVENT.is_set():
            raise SegmentAborted("arrêt de l'application demandé")  # FFmpeg est tué par le finally
        ts = decode_start + k * interval  # horodatage GLOBAL dans le film
        if ts < task.start_ms:
            continue  # image de pré-lecture : appartient au segment précédent
        if ts >= task.end_ms:
            break  # règle de propriété [start, end)
        t0 = time.perf_counter()
        value, info = phash_gray(frame)
        hash_s += time.perf_counter() - t0
        result.timestamps.append(ts)
        result.hashes.append(value)
        result.informativeness.append(round(info, 3))
    result.frames_decoded = stats.frames
    result.decode_s = stats.read_s
    result.hash_s = hash_s
    result.wall_s = time.perf_counter() - t_start
    if not result.hashes and task.end_ms - task.start_ms >= interval:
        # FFmpeg peut sortir avec le code 0 sur un fichier tronqué : un segment vide n'est
        # jamais accepté silencieusement (sinon l'index serait partiel sans le savoir).
        raise FFmpegError(f"aucune image décodée entre {task.start_ms} et {task.end_ms} ms "
                          "(fichier tronqué ou corrompu ?)")
    return result


def worker_initializer(abort_event=None, barrier=None) -> None:  # noqa: ANN001
    """Initialisation de chaque processus du pool.

    * limite les threads internes : sans cela OpenCV/NumPy pourraient chacun lancer autant
      de threads que de cœurs dans *chaque* processus (N processus x 12 threads) ;
    * mémorise l'``Event`` d'arrêt et la ``Barrier`` de préchauffage (primitives
      ``multiprocessing`` transmises par héritage au démarrage du processus).
    """
    global _ABORT_EVENT, _BARRIER
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")
    import cv2

    cv2.setNumThreads(1)
    _ABORT_EVENT = abort_event
    _BARRIER = barrier


def worker_ping(wait_barrier: bool = False) -> int:
    """Préchauffe un processus et renvoie son PID.

    Avec ``wait_barrier=True``, chaque appel attend que N appels soient en cours en même
    temps (``Barrier`` à N participants) : cela **prouve** que N processus distincts sont
    vivants, sans ``sleep()`` arbitraire.
    """
    if wait_barrier and _BARRIER is not None:
        _BARRIER.wait(timeout=120)
    return os.getpid()
