from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from functools import cache
from pathlib import Path

import numpy as np

FRAME_SIZE = 64
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class FFmpegError(RuntimeError):
    pass


class FFmpegNotFound(FFmpegError):
    pass


class InvalidVideo(FFmpegError):
    pass


@cache
def find_binary(name: str) -> str:
    env = os.environ.get(f"{name.upper()}_BINARY")
    if env:
        if Path(env).is_file():
            return env
        raise FFmpegNotFound(f"{name.upper()}_BINARY pointe vers un fichier inexistant : {env}")
    found = shutil.which(name)
    if found:
        return found
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
        for cand in sorted(base.glob(f"Gyan.FFmpeg*/*/bin/{name}.exe"), reverse=True):
            return str(cand)
    raise FFmpegNotFound(
        f"{name} introuvable. Installez FFmpeg (Windows : winget install Gyan.FFmpeg ; "
        f"Ubuntu : sudo apt install ffmpeg ; macOS : brew install ffmpeg) puis rouvrez le terminal."
    )


_VERSION: str | None = None


def ffmpeg_version() -> str:
    global _VERSION
    if _VERSION is not None:
        return _VERSION
    try:
        out = subprocess.run([find_binary("ffmpeg"), "-version"], capture_output=True, timeout=10,
                             creationflags=_NO_WINDOW)
        _VERSION = out.stdout.decode(errors="replace").splitlines()[0]
        return _VERSION
    except (FFmpegError, OSError, subprocess.SubprocessError, IndexError) as exc:
        return f"indisponible ({exc})"


@dataclass(frozen=True)
class VideoInfo:
    duration_ms: int
    width: int
    height: int
    codec: str
    fps: float
    format_name: str
    bit_rate: int | None
    nb_frames: int | None

    def to_dict(self) -> dict:
        return asdict(self)


def _parse_rate(rate: str | None) -> float:
    if not rate or rate in ("0/0", "N/A"):
        return 0.0
    if "/" in rate:
        num, den = rate.split("/", 1)
        return float(num) / float(den) if float(den) else 0.0
    return float(rate)


def probe_video(path: str | Path, timeout_s: float = 30.0) -> VideoInfo:
    cmd = [find_binary("ffprobe"), "-v", "error", "-print_format", "json",
           "-show_format", "-show_streams", str(path)]
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=timeout_s, creationflags=_NO_WINDOW)
    except subprocess.TimeoutExpired as exc:
        raise InvalidVideo(f"ffprobe a dépassé {timeout_s:.0f} s") from exc
    if res.returncode != 0:
        msg = res.stderr.decode(errors="replace").strip()[-400:]
        raise InvalidVideo(f"fichier vidéo illisible ({msg or 'ffprobe a échoué'})")
    try:
        data = json.loads(res.stdout or b"{}")
    except json.JSONDecodeError as exc:
        raise InvalidVideo("réponse ffprobe invalide") from exc
    streams = [s for s in data.get("streams", []) if s.get("codec_type") == "video"
               and s.get("disposition", {}).get("attached_pic", 0) == 0]
    if not streams:
        raise InvalidVideo("aucun flux vidéo dans ce fichier")
    vs = streams[0]
    fmt = data.get("format", {})
    duration = vs.get("duration") or fmt.get("duration")
    try:
        duration_ms = int(round(float(duration) * 1000))
    except (TypeError, ValueError) as exc:
        raise InvalidVideo("durée de la vidéo inconnue") from exc
    if duration_ms <= 0:
        raise InvalidVideo("durée de la vidéo nulle")
    fps = _parse_rate(vs.get("avg_frame_rate")) or _parse_rate(vs.get("r_frame_rate"))
    nb = vs.get("nb_frames")
    br = fmt.get("bit_rate")
    return VideoInfo(
        duration_ms=duration_ms,
        width=int(vs.get("width") or 0),
        height=int(vs.get("height") or 0),
        codec=str(vs.get("codec_name") or "inconnu"),
        fps=round(fps, 3),
        format_name=str(fmt.get("format_name") or "inconnu"),
        bit_rate=int(br) if br and str(br).isdigit() else None,
        nb_frames=int(nb) if nb and str(nb).isdigit() else None,
    )


@dataclass
class DecodeStats:
    frames: int = 0
    read_s: float = 0.0


def iter_gray_frames(
    path: str | Path,
    *,
    start_ms: int,
    duration_ms: int | None,
    fps: float,
    size: int = FRAME_SIZE,
    threads: int = 1,
    timeout_s: float = 600.0,
    stats: DecodeStats | None = None,
    ffmpeg_bin: str | None = None,
) -> Iterator[tuple[int, np.ndarray]]:
    import time

    ffmpeg = ffmpeg_bin or find_binary("ffmpeg")
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-threads", str(threads)]
    if start_ms > 0:
        cmd += ["-ss", f"{start_ms / 1000:.3f}"]
    cmd += ["-i", str(path)]
    if duration_ms is not None:
        cmd += ["-t", f"{duration_ms / 1000:.3f}"]
    cmd += ["-map", "0:v:0", "-an", "-sn", "-dn", "-filter_threads", "1",
            "-vf", f"fps=fps={fps}:start_time=0:round=up,scale={size}:{size}:flags=area,format=gray",
            "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"]

    frame_bytes = size * size
    err_file = tempfile.TemporaryFile()  # noqa: SIM115 - fermé dans le finally du générateur
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err_file, stdin=subprocess.DEVNULL,
                            bufsize=frame_bytes * 16, creationflags=_NO_WINDOW)
    timed_out = threading.Event()

    def _watchdog() -> None:
        timed_out.set()
        proc.kill()

    timer = threading.Timer(timeout_s, _watchdog)
    timer.daemon = True
    timer.start()
    finished_normally = False
    try:
        assert proc.stdout is not None
        index = 0
        while True:
            t0 = time.perf_counter()
            buf = proc.stdout.read(frame_bytes)
            if stats is not None:
                stats.read_s += time.perf_counter() - t0
            if not buf:
                break
            if len(buf) < frame_bytes:
                break
            if stats is not None:
                stats.frames += 1
            yield index, np.frombuffer(buf, dtype=np.uint8).reshape(size, size)
            index += 1
        proc.wait(timeout=30)
        finished_normally = True
    finally:
        timer.cancel()
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        if proc.stdout:
            proc.stdout.close()
        err_file.seek(0)
        stderr = err_file.read()[-2000:].decode(errors="replace").strip()
        err_file.close()
    if timed_out.is_set():
        raise FFmpegError(f"FFmpeg a dépassé le délai de {timeout_s:.0f} s")
    if finished_normally and proc.returncode != 0:
        raise FFmpegError(f"FFmpeg a échoué (code {proc.returncode}) : {stderr[-500:] or 'erreur inconnue'}")


def extract_thumbnail(
    path: str | Path, dest: str | Path, at_ms: int, width: int = 480, timeout_s: float = 60.0
) -> None:
    dest = Path(dest)
    tmp = dest.with_name(dest.stem + ".tmp" + dest.suffix)
    cmd = [find_binary("ffmpeg"), "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
           "-ss", f"{max(at_ms, 0) / 1000:.3f}", "-i", str(path), "-frames:v", "1",
           "-vf", f"scale={width}:-2", "-q:v", "4", str(tmp)]
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=timeout_s, creationflags=_NO_WINDOW)
    except subprocess.TimeoutExpired as exc:
        tmp.unlink(missing_ok=True)
        raise FFmpegError("extraction de vignette trop longue") from exc
    if res.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        raise FFmpegError(f"vignette impossible : {res.stderr.decode(errors='replace')[-300:]}")
    os.replace(tmp, dest)
