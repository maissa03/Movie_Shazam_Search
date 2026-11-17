"""Génère un corpus vidéo synthétique, déterministe et libre de droits pour MovieShazam.

Ce script produit :
  * des « films » synthétiques (plans texturés, mouvements de caméra, formes mobiles,
    changements de plans, logo de studio commun, scène sombre, scène répétée,
    passage partagé entre deux films, générique de fin) ;
  * des extraits (clips) de 5 à 20 s, à des instants variés (début, milieu, fin,
    début non aligné sur la seconde, franchissement de frontière de segment) ;
  * des variantes transformées (recompression, réduction de résolution, FPS modifié) ;
  * des extraits « inconnus » provenant de films jamais indexés ;
  * ``ground_truth.json`` : vérité terrain (film d'origine, début/fin réels,
    transformation, split validation/test, résultat attendu).

Tout est déterministe : même graine => mêmes images.  Les images sont rendues une
par une puis envoyées à FFmpeg par un pipe : aucun film n'est gardé en mémoire.

Usage :
    python scripts/generate_demo_videos.py                # corpus de démo (1280x720)
    python scripts/generate_demo_videos.py --profile mini --out data/mini
    python scripts/generate_demo_videos.py --force        # régénère tout
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.video.ffmpeg_io import find_binary, probe_video  # noqa: E402

GENERATOR_VERSION = "demo-gen-v1"

COLORMAPS = [
    cv2.COLORMAP_JET, cv2.COLORMAP_OCEAN, cv2.COLORMAP_HOT, cv2.COLORMAP_VIRIDIS,
    cv2.COLORMAP_PLASMA, cv2.COLORMAP_TWILIGHT, cv2.COLORMAP_TURBO, cv2.COLORMAP_INFERNO,
    cv2.COLORMAP_SPRING, cv2.COLORMAP_COOL, cv2.COLORMAP_PARULA, cv2.COLORMAP_MAGMA,
    cv2.COLORMAP_DEEPGREEN, cv2.COLORMAP_RAINBOW, cv2.COLORMAP_AUTUMN, cv2.COLORMAP_SUMMER,
    cv2.COLORMAP_WINTER, cv2.COLORMAP_PINK,
]
STYLES = ["texture", "geometric", "stripes", "particles"]


# --------------------------------------------------------------------------- #
# Rendu des plans                                                              #
# --------------------------------------------------------------------------- #
def _smooth_noise(rng: np.random.Generator, h: int, w: int, octaves: int = 3) -> np.ndarray:
    """Bruit lisse multi-octaves (valeurs 0..255, uint8)."""
    acc = np.zeros((h, w), np.float32)
    for o in range(octaves):
        gh = int(3 * 2**o + rng.integers(0, 3))
        gw = max(2, int(round(gh * w / h)))
        grid = rng.random((gh, gw)).astype(np.float32)
        acc += cv2.resize(grid, (w, h), interpolation=cv2.INTER_CUBIC) / (2**o)
    acc -= acc.min()
    acc /= max(float(acc.max()), 1e-6)
    return (acc * 255).astype(np.uint8)


def _bright_color(rng: np.random.Generator) -> tuple[int, int, int]:
    hsv = np.uint8([[[rng.integers(0, 180), rng.integers(150, 256), rng.integers(170, 256)]]])
    b, g, r = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)[0, 0]
    return int(b), int(g), int(r)


def _bounce(x0: float, v: float, t: float, lo: float, hi: float) -> float:
    """Mouvement rectiligne avec rebonds entre lo et hi (onde triangulaire)."""
    span = max(hi - lo, 1e-6)
    p = (x0 - lo + v * t) % (2 * span)
    return lo + (p if p <= span else 2 * span - p)


@dataclass
class Shape:
    kind: str
    x0: float
    y0: float
    vx: float
    vy: float
    size: float
    color: tuple[int, int, int]
    angle0: float
    omega: float

    def draw(self, img: np.ndarray, t: float, dim: float = 1.0) -> None:
        h, w = img.shape[:2]
        x = _bounce(self.x0, self.vx, t, 0, w)
        y = _bounce(self.y0, self.vy, t, 0, h)
        color = tuple(int(c * dim) for c in self.color)
        s = self.size
        if self.kind == "circle":
            cv2.circle(img, (int(x), int(y)), int(s), color, -1, cv2.LINE_AA)
        elif self.kind == "ring":
            cv2.circle(img, (int(x), int(y)), int(s), color, max(3, int(s / 5)), cv2.LINE_AA)
        else:
            angle = self.angle0 + self.omega * t
            box = cv2.boxPoints(((x, y), (s * 1.8, s * (1.0 if self.kind == "rect" else 0.5)), angle))
            if self.kind == "tri":
                box = box[:3]
            cv2.fillPoly(img, [box.astype(np.int32)], color, cv2.LINE_AA)


def _random_shapes(rng: np.random.Generator, w: int, h: int, n: int) -> list[Shape]:
    scale = w / 1280
    shapes = []
    for _ in range(n):
        shapes.append(
            Shape(
                kind=str(rng.choice(["circle", "rect", "tri", "ring", "bar"])),
                x0=float(rng.uniform(0, w)),
                y0=float(rng.uniform(0, h)),
                vx=float(rng.uniform(-260, 260) * scale),
                vy=float(rng.uniform(-200, 200) * scale),
                size=float(rng.uniform(30, 150) * scale),
                color=_bright_color(rng),
                angle0=float(rng.uniform(0, 360)),
                omega=float(rng.uniform(-90, 90)),
            )
        )
    return shapes


class ShotRenderer:
    """Un plan : fonction déterministe t_local -> image BGR."""

    def __init__(self, style: str, seed: int, w: int, h: int, dim: float = 1.0):
        self.style, self.w, self.h, self.dim = style, w, h, dim
        rng = np.random.default_rng(seed)
        self.rng_seed = seed
        if style in ("texture", "dark"):
            th, tw = int(h * 1.7), int(w * 1.7)
            gray = _smooth_noise(rng, th, tw, octaves=3)
            self.texture = cv2.applyColorMap(gray, COLORMAPS[int(rng.integers(len(COLORMAPS)))])
            self.zoom0 = float(rng.uniform(0.6, 0.95))
            self.zoom1 = float(np.clip(self.zoom0 + rng.uniform(-0.25, 0.25), 0.5, 1.0))
            self.cx0 = float(rng.uniform(0, tw - w))
            self.cy0 = float(rng.uniform(0, th - h))
            self.vcx = float(rng.uniform(-160, 160) * w / 1280)
            self.vcy = float(rng.uniform(-110, 110) * w / 1280)
            self.shapes = _random_shapes(rng, w, h, int(rng.integers(3, 7)))
        elif style == "geometric":
            c1 = np.array(_bright_color(rng), np.float32)
            c2 = np.array(_bright_color(rng), np.float32) * 0.4
            ang = rng.uniform(0, 2 * math.pi)
            yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
            g = (xx * math.cos(ang) + yy * math.sin(ang))
            g = (g - g.min()) / max(float(g.max() - g.min()), 1e-6)
            blend = c1[None, None, :] * g[..., None] + c2[None, None, :] * (1 - g[..., None])
            self.background = blend.astype(np.uint8)
            for _ in range(int(rng.integers(4, 9))):  # gros blocs statiques
                x1, y1 = int(rng.uniform(0, w)), int(rng.uniform(0, h))
                x2, y2 = x1 + int(rng.uniform(0.1, 0.4) * w), y1 + int(rng.uniform(0.1, 0.4) * h)
                cv2.rectangle(self.background, (x1, y1), (x2, y2), _bright_color(rng), -1)
            self.shapes = _random_shapes(rng, w, h, int(rng.integers(6, 12)))
        elif style == "stripes":
            sh, sw = h // 4, w // 4
            yy, xx = np.mgrid[0:sh, 0:sw].astype(np.float32)
            theta = rng.uniform(0, math.pi)
            freq = rng.uniform(2.0, 7.0) / sw
            self.phase = 2 * math.pi * freq * (xx * math.cos(theta) + yy * math.sin(theta))
            self.omega = float(rng.uniform(1.5, 4.0))
            self.ca = np.array(_bright_color(rng), np.float32)
            self.cb = np.array(_bright_color(rng), np.float32) * 0.3
            self.shapes = _random_shapes(rng, w, h, int(rng.integers(2, 5)))
        elif style == "particles":
            yy = np.linspace(0, 1, h, dtype=np.float32)[:, None, None]
            top = np.array(_bright_color(rng), np.float32) * 0.25
            self.background = np.broadcast_to(top * (1 - yy), (h, w, 3)).astype(np.uint8)
            n = int(rng.integers(120, 220))
            self.stars = np.column_stack([
                rng.uniform(0, w, n), rng.uniform(0, h, n),
                rng.uniform(-120, 120, n) * w / 1280, rng.uniform(-60, 60, n) * w / 1280,
                rng.uniform(1.5, 4.5, n) * w / 1280,
            ])
            self.planets = _random_shapes(rng, w, h, int(rng.integers(2, 4)))
            for p in self.planets:
                p.kind, p.size = "circle", p.size * 1.6
        else:
            raise ValueError(style)

    def frame(self, t: float) -> np.ndarray:
        w, h = self.w, self.h
        if self.style in ("texture", "dark"):
            tex_h, tex_w = self.texture.shape[:2]
            z = self.zoom0 + (self.zoom1 - self.zoom0) * min(t / 8.0, 1.0)
            cx = _bounce(self.cx0, self.vcx, t, 0, tex_w - w * z)
            cy = _bounce(self.cy0, self.vcy, t, 0, tex_h - h * z)
            m = np.float32([[z, 0, cx], [0, z, cy]])
            img = cv2.warpAffine(self.texture, m, (w, h), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP)
            for s in self.shapes:
                s.draw(img, t)
            if self.style == "dark":
                img = (img.astype(np.float32) * self.dim).astype(np.uint8)
            return img
        if self.style == "geometric":
            img = self.background.copy()
            for s in self.shapes:
                s.draw(img, t)
            return img
        if self.style == "stripes":
            v = (np.sin(self.phase - self.omega * t) * 0.5 + 0.5)[..., None]
            small = (self.ca * v + self.cb * (1 - v)).astype(np.uint8)
            img = cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)
            for s in self.shapes:
                s.draw(img, t)
            return img
        # particles
        img = self.background.copy()
        st = self.stars
        xs = (st[:, 0] + st[:, 2] * t) % w
        ys = (st[:, 1] + st[:, 3] * t) % h
        for x, y, r in zip(xs, ys, st[:, 4]):
            cv2.circle(img, (int(x), int(y)), max(1, int(r)), (255, 255, 255), -1, cv2.LINE_AA)
        for p in self.planets:
            p.draw(img, t)
        return img


def _render_logo(t: float, w: int, h: int) -> np.ndarray:
    """Logo de studio commun à TOUS les films (contenu répété volontairement)."""
    img = np.zeros((h, w, 3), np.uint8)
    alpha = min(1.0, t / 0.5)
    s = w / 1280
    center = (w // 2, int(h * 0.42))
    cv2.circle(img, center, int(110 * s), (40, 180, 255), int(14 * s), cv2.LINE_AA)
    tri = np.array([[w // 2 - int(60 * s), int(h * 0.42) + int(45 * s)],
                    [w // 2 + int(60 * s), int(h * 0.42) + int(45 * s)],
                    [w // 2, int(h * 0.42) - int(70 * s)]], np.int32)
    cv2.fillPoly(img, [tri], (255, 255, 255), cv2.LINE_AA)
    text = "MOVIESHAZAM DEMO STUDIO"
    scale = 1.6 * s
    (tw, _), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_DUPLEX, scale, max(1, int(3 * s)))
    cv2.putText(img, text, ((w - tw) // 2, int(h * 0.78)), cv2.FONT_HERSHEY_DUPLEX, scale,
                (230, 230, 230), max(1, int(3 * s)), cv2.LINE_AA)
    return (img.astype(np.float32) * alpha).astype(np.uint8)


def _render_credits(t: float, w: int, h: int, lines: list[str]) -> np.ndarray:
    """Générique de fin défilant (texte blanc sur noir)."""
    img = np.zeros((h, w, 3), np.uint8)
    s = w / 1280
    line_h = int(70 * s)
    y0 = h - int(t * 160 * s)
    for i, line in enumerate(lines):
        y = y0 + i * line_h
        if -line_h < y < h + line_h:
            scale = (1.5 if i < 2 else 1.0) * s
            (tw, _), _ = cv2.getTextSize(line, cv2.FONT_HERSHEY_SIMPLEX, scale, max(1, int(2 * s)))
            cv2.putText(img, line, ((w - tw) // 2, y), cv2.FONT_HERSHEY_SIMPLEX, scale,
                        (255, 255, 255), max(1, int(2 * s)), cv2.LINE_AA)
    return img


# --------------------------------------------------------------------------- #
# Films : timeline = liste d'intervalles [start, end) -> rendu                 #
# --------------------------------------------------------------------------- #
@dataclass
class Interval:
    start: float
    end: float
    kind: str  # black | logo | shot | credits | repeat | borrow
    style: str = ""
    seed: int = 0
    ref: str = ""        # identifiant du plan répété / clé du film emprunté
    ref_start: float = 0.0
    dim: float = 1.0


@dataclass
class FilmSpec:
    key: str
    slug: str
    title: str
    year: int
    duration: float
    seed: int
    indexed: bool
    anchors: list[Interval] = field(default_factory=list)


class Film:
    def __init__(self, spec: FilmSpec, w: int, h: int, films: dict[str, Film]):
        self.spec, self.w, self.h, self.films = spec, w, h, films
        self.timeline = self._build_timeline()
        self._cache: dict[tuple[str, int], ShotRenderer] = {}
        rng = np.random.default_rng(spec.seed + 999)
        syll = ["ma", "ri", "lo", "sa", "ne", "ka", "to", "be", "li", "mo", "ra", "di"]
        def name() -> str:
            return "".join(rng.choice(syll, 3)).capitalize() + " " + "".join(rng.choice(syll, 2)).upper()
        ascii_title = spec.title.encode("ascii", "ignore").decode() or spec.key
        self.credits = ["FIN", ascii_title.upper(), "", f"Realisation  {name()}", f"Image  {name()}",
                        f"Montage  {name()}", f"Musique  {name()}", f"Production  {name()}",
                        "", "Video synthetique generee", "pour MovieShazam"]

    def _build_timeline(self) -> list[Interval]:
        spec = self.spec
        rng = np.random.default_rng(spec.seed)
        fixed = [Interval(0.0, 1.0, "black"), Interval(1.0, 3.0, "logo")]
        fixed += sorted(spec.anchors, key=lambda a: a.start)
        fixed.append(Interval(spec.duration - 4.0, spec.duration, "credits"))
        timeline: list[Interval] = []
        shot_no = 0
        for a, b in zip(fixed, fixed[1:]):
            timeline.append(a)
            t = a.end
            while b.start - t > 1e-9:  # remplir le trou par des plans aléatoires 3–7 s
                d = float(rng.uniform(3.0, 7.0))
                if b.start - (t + d) < 2.0:
                    d = b.start - t
                style = STYLES[int(rng.integers(len(STYLES)))]
                timeline.append(Interval(t, t + d, "shot", style=style, seed=spec.seed * 1000 + shot_no))
                shot_no += 1
                t += d
        timeline.append(fixed[-1])
        return timeline

    def _renderer(self, style: str, seed: int, dim: float = 1.0) -> ShotRenderer:
        key = (style, seed)
        if key not in self._cache:
            if len(self._cache) > 3:
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = ShotRenderer(style, seed, self.w, self.h, dim)
        return self._cache[key]

    def _find(self, t: float) -> Interval:
        for iv in self.timeline:
            if iv.start <= t < iv.end:
                return iv
        return self.timeline[-1]

    def frame_at(self, t: float) -> np.ndarray:
        iv = self._find(t)
        local = t - iv.start
        if iv.kind == "black":
            return np.zeros((self.h, self.w, 3), np.uint8)
        if iv.kind == "logo":
            return _render_logo(local, self.w, self.h)
        if iv.kind == "credits":
            return _render_credits(local, self.w, self.h, self.credits)
        if iv.kind == "shot":
            return self._renderer(iv.style, iv.seed, iv.dim).frame(local)
        if iv.kind == "repeat":  # rejoue exactement un plan précédent du même film
            src = next(x for x in self.timeline if x.ref == iv.ref and x.kind == "shot")
            return self._renderer(src.style, src.seed, src.dim).frame(local)
        if iv.kind == "borrow":  # passage copié depuis un autre film
            return self.films[iv.ref].frame_at(iv.ref_start + local)
        raise ValueError(iv.kind)


# --------------------------------------------------------------------------- #
# Profils de corpus                                                            #
# --------------------------------------------------------------------------- #
@dataclass
class ClipSpec:
    split: str
    film: str
    start: float
    duration: float
    transform: str  # clean | recompress | downscale | fps15 | fps30 | combo
    expected: str   # match | no_match | ambiguous
    note: str = ""
    alternatives: list[tuple[str, float]] = field(default_factory=list)


def demo_profile() -> tuple[list[FilmSpec], list[ClipSpec], dict]:
    films = [
        FilmSpec("A", "A_ocean_de_verre", "Océan de Verre", 2024, 120.0, 11, True, anchors=[
            Interval(30.0, 36.0, "shot", style="texture", seed=11_777, ref="A_rep"),
            Interval(90.0, 96.0, "repeat", ref="A_rep"),  # scène répétée (flashback)
        ]),
        FilmSpec("B", "B_foret_electrique", "Forêt Électrique", 2023, 90.0, 22, True),
        FilmSpec("C", "C_nuit_polaire", "Nuit Polaire", 2025, 75.0, 33, True, anchors=[
            Interval(24.0, 32.0, "shot", style="dark", seed=33_555, dim=0.16),  # scène sombre
        ]),
        FilmSpec("D", "D_ville_fractale", "Ville Fractale", 2026, 60.0, 44, True, anchors=[
            Interval(20.0, 28.0, "borrow", ref="B", ref_start=40.0),  # passage partagé avec B
        ]),
        FilmSpec("E", "E_inconnu_validation", "Inconnu (validation)", 2026, 30.0, 55, False),
        FilmSpec("F", "F_inconnu_test", "Inconnu (test)", 2026, 30.0, 66, False),
    ]
    V, T = "validation", "test"
    clips = [
        # ---- validation : sert à calibrer les seuils ----
        ClipSpec(V, "A", 7.0, 10, "clean", "match"),
        ClipSpec(V, "A", 63.3, 10, "recompress", "match", "début non aligné"),
        ClipSpec(V, "B", 12.7, 5, "clean", "match", "clip court 5 s"),
        ClipSpec(V, "B", 55.0, 20, "downscale", "match"),
        ClipSpec(V, "C", 36.4, 10, "fps15", "match"),
        ClipSpec(V, "C", 60.0, 10, "combo", "match"),
        ClipSpec(V, "D", 35.2, 10, "clean", "match"),
        ClipSpec(V, "D", 44.0, 12, "recompress", "match"),
        ClipSpec(V, "D", 20.5, 7, "clean", "ambiguous", "passage partagé D[20-28]=B[40-48]",
                 alternatives=[("B", 40.5)]),
        ClipSpec(V, "E", 0.5, 10, "clean", "no_match", "film non indexé (contient le logo commun)"),
        ClipSpec(V, "E", 18.2, 5, "recompress", "no_match", "film non indexé"),
        ClipSpec(V, "E", 9.0, 15, "downscale", "no_match", "film non indexé"),
        # ---- test final : jamais utilisé pour régler les seuils ----
        ClipSpec(T, "A", 0.0, 10, "clean", "match", "début du film (noir + logo commun)"),
        ClipSpec(T, "A", 44.6, 10, "clean", "match", "traverse une frontière de segment (45 s)"),
        ClipSpec(T, "A", 105.0, 15, "clean", "match", "fin du film (générique)"),
        ClipSpec(T, "A", 80.25, 10, "combo", "match", "480x270 + 15 fps + CRF 30"),
        ClipSpec(T, "A", 30.5, 5, "clean", "ambiguous", "scène répétée A[30-36]=A[90-96]",
                 alternatives=[("A", 90.5)]),
        ClipSpec(T, "B", 3.0, 5, "clean", "match"),
        ClipSpec(T, "B", 70.3, 10, "recompress", "match"),
        ClipSpec(T, "B", 76.0, 14, "downscale", "match", "fin du film"),
        ClipSpec(T, "C", 24.5, 10, "clean", "match", "scène sombre"),
        ClipSpec(T, "C", 25.0, 6, "recompress", "match", "scène sombre recompressée"),
        ClipSpec(T, "C", 50.75, 20, "downscale", "match", "traverse 2 frontières de segment"),
        ClipSpec(T, "D", 2.0, 10, "fps30", "match", "FPS 25 -> 30"),
        ClipSpec(T, "D", 18.0, 15, "clean", "match", "contient le passage partagé + contexte propre à D"),
        ClipSpec(T, "F", 3.0, 10, "clean", "no_match", "film non indexé"),
        ClipSpec(T, "F", 15.5, 10, "recompress", "no_match", "film non indexé"),
        ClipSpec(T, "F", 0.0, 8, "combo", "no_match", "film non indexé (noir + logo commun)"),
    ]
    video = {"width": 1280, "height": 720, "fps": 25, "crf": 23}
    return films, clips, video


def mini_profile() -> tuple[list[FilmSpec], list[ClipSpec], dict]:
    """Petit corpus rapide pour les tests unitaires/intégration."""
    films = [
        FilmSpec("A", "A_mini", "Mini A", 2026, 36.0, 101, True),
        FilmSpec("B", "B_mini", "Mini B", 2026, 30.0, 202, True, anchors=[
            Interval(12.0, 18.0, "shot", style="dark", seed=202_333, dim=0.18)]),
        FilmSpec("U", "U_mini_inconnu", "Mini inconnu", 2026, 20.0, 303, False),
    ]
    clips = [
        ClipSpec("test", "A", 5.3, 8, "clean", "match"),
        ClipSpec("test", "A", 19.0, 10, "recompress", "match", "traverse une frontière"),
        ClipSpec("test", "B", 12.5, 6, "clean", "match", "scène sombre"),
        ClipSpec("test", "B", 20.0, 8, "downscale", "match"),
        ClipSpec("test", "U", 4.0, 8, "clean", "no_match"),
    ]
    video = {"width": 320, "height": 180, "fps": 25, "crf": 23}
    return films, clips, video


PROFILES: dict[str, Callable[[], tuple[list[FilmSpec], list[ClipSpec], dict]]] = {
    "demo": demo_profile,
    "mini": mini_profile,
}

TRANSFORMS: dict[str, dict] = {
    "clean": {"vf": None, "crf": 18, "label": "réencodage propre (CRF 18)"},
    "recompress": {"vf": None, "crf": 33, "label": "recompression forte H.264 (CRF 33)"},
    "downscale": {"vf": "scale=640:-2", "crf": 26, "label": "640 px de large, CRF 26"},
    "fps15": {"vf": "fps=15", "crf": 23, "label": "15 images/s"},
    "fps30": {"vf": "fps=30", "crf": 23, "label": "30 images/s"},
    "combo": {"vf": "scale=480:-2,fps=15", "crf": 30, "label": "480 px + 15 images/s + CRF 30"},
}


# --------------------------------------------------------------------------- #
# Encodage                                                                     #
# --------------------------------------------------------------------------- #
def render_film(film: Film, out: Path, fps: int, crf: int) -> None:
    ffmpeg = find_binary("ffmpeg")
    w, h = film.w, film.h
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}", "-r", str(fps), "-i", "pipe:0",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf), "-pix_fmt", "yuv420p",
           "-g", str(fps * 2), "-movflags", "+faststart", str(out)]
    n_frames = int(round(film.spec.duration * fps))
    tmp = out.with_suffix(".part.mp4")
    cmd[-1] = str(tmp)
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdin is not None
    try:
        for i in range(n_frames):
            proc.stdin.write(film.frame_at(i / fps).tobytes())
        proc.stdin.close()
        _, err = proc.communicate(timeout=600)
    except BaseException:
        proc.kill()
        proc.wait()
        tmp.unlink(missing_ok=True)
        raise
    if proc.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg a échoué pour {out.name}: {err.decode(errors='replace')[-800:]}")
    tmp.replace(out)


def extract_clip(src: Path, dst: Path, start: float, duration: float, transform: str) -> None:
    ffmpeg = find_binary("ffmpeg")
    tr = TRANSFORMS[transform]
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}", "-i", str(src),
           "-t", f"{duration:.3f}", "-an"]
    if tr["vf"]:
        cmd += ["-vf", tr["vf"]]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(tr["crf"]), "-pix_fmt", "yuv420p", str(dst)]
    res = subprocess.run(cmd, capture_output=True, timeout=300)
    if res.returncode != 0:
        raise RuntimeError(f"extraction du clip {dst.name} impossible: {res.stderr.decode(errors='replace')[-800:]}")


def generate(profile: str, out_dir: Path, force: bool = False, seed_offset: int = 0) -> dict:
    film_specs, clip_specs, video = PROFILES[profile]()
    if seed_offset:
        for f in film_specs:
            f.seed += seed_offset
    params = {"generator": GENERATOR_VERSION, "profile": profile, "video": video, "seed_offset": seed_offset}
    fingerprint = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()[:16]
    gt_path = out_dir / "ground_truth.json"
    if gt_path.exists() and not force:
        existing = json.loads(gt_path.read_text(encoding="utf-8"))
        if existing.get("params_hash") == fingerprint and all(
            (out_dir / m["file"]).exists() for m in existing["movies"]
        ) and all((out_dir / c["file"]).exists() for c in existing["clips"]):
            print(f"[ok] corpus déjà présent et à jour : {gt_path}")
            return existing

    (out_dir / "movies").mkdir(parents=True, exist_ok=True)
    (out_dir / "unknown").mkdir(parents=True, exist_ok=True)
    (out_dir / "clips").mkdir(parents=True, exist_ok=True)

    w, h, fps, crf = video["width"], video["height"], video["fps"], video["crf"]
    films: dict[str, Film] = {}
    for spec in film_specs:
        films[spec.key] = Film(spec, w, h, films)

    movie_entries = []
    for spec in film_specs:
        sub = "movies" if spec.indexed else "unknown"
        path = out_dir / sub / f"{spec.slug}.mp4"
        t0 = time.perf_counter()
        render_film(films[spec.key], path, fps, crf)
        info = probe_video(path)
        print(f"[film] {path.name:32s} {spec.duration:6.1f} s  {path.stat().st_size / 1e6:6.1f} Mo  "
              f"({time.perf_counter() - t0:.1f} s)")
        movie_entries.append({
            "key": spec.key, "title": spec.title, "year": spec.year, "file": f"{sub}/{path.name}",
            "duration_ms": info.duration_ms, "indexed": spec.indexed,
            "timeline": [
                {"start_s": round(iv.start, 3), "end_s": round(iv.end, 3), "kind": iv.kind,
                 "style": iv.style or None, "ref": iv.ref or None}
                for iv in films[spec.key].timeline
            ],
        })

    clip_entries = []
    by_key = {m["key"]: m for m in movie_entries}
    for spec in clip_specs:
        src = out_dir / by_key[spec.film]["file"]
        cid = f"{spec.split[:3]}_{spec.film}_{int(round(spec.start * 1000)):06d}_{int(spec.duration)}s_{spec.transform}"
        dst = out_dir / "clips" / f"{cid}.mp4"
        extract_clip(src, dst, spec.start, spec.duration, spec.transform)
        entry = {
            "id": cid, "file": f"clips/{dst.name}", "split": spec.split, "expected": spec.expected,
            "source_key": spec.film, "source_title": by_key[spec.film]["title"],
            "source_indexed": by_key[spec.film]["indexed"],
            "start_ms": int(round(spec.start * 1000)), "end_ms": int(round((spec.start + spec.duration) * 1000)),
            "transform": spec.transform, "transform_label": TRANSFORMS[spec.transform]["label"],
            "note": spec.note,
            "alternatives": [{"source_key": k, "start_ms": int(round(s * 1000))} for k, s in spec.alternatives],
        }
        clip_entries.append(entry)
        print(f"[clip] {dst.name}")

    gt = {"params": params, "params_hash": fingerprint, "movies": movie_entries, "clips": clip_entries,
          "tolerance_ms": 1000,
          "notes": "Vérité terrain générée automatiquement ; start_ms = instant du film où commence le clip."}
    gt_path.write_text(json.dumps(gt, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[ok] {len(movie_entries)} films, {len(clip_entries)} clips -> {gt_path}")
    return gt


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", choices=sorted(PROFILES), default="demo")
    ap.add_argument("--out", type=Path, default=None, help="dossier de sortie (défaut: data/demo ou data/mini)")
    ap.add_argument("--force", action="store_true", help="régénérer même si le corpus existe")
    args = ap.parse_args()
    out = args.out or (ROOT / "data" / args.profile)
    t0 = time.perf_counter()
    generate(args.profile, out.resolve(), force=args.force)
    print(f"Terminé en {time.perf_counter() - t0:.1f} s")


if __name__ == "__main__":
    main()
