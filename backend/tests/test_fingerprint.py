"""(1) pHash déterministe et distance de Hamming ; (2) stockage stable des 64 bits."""

from __future__ import annotations

import random

import cv2
import numpy as np
import pytest

from app.video.fingerprint import (
    band_probes,
    bands,
    blobs_to_array,
    dct_matrix,
    from_blob,
    hamming,
    hamming_many,
    phash_gray,
    to_blob,
    to_hex,
)


def _textured(seed: int, size: int = 64) -> np.ndarray:
    rng = np.random.default_rng(seed)
    small = rng.random((6, 6)).astype(np.float32)
    img = cv2.resize(small, (size, size), interpolation=cv2.INTER_CUBIC)
    return np.clip(img * 255, 0, 255).astype(np.uint8)


def test_phash_is_deterministic():
    img = _textured(1)
    assert phash_gray(img) == phash_gray(img.copy())
    assert phash_gray(img)[0] == phash_gray(img)[0]


def test_phash_matches_reference_definition():
    """Recalcule le hash avec une DCT NumPy pure : même convention bit à bit."""
    img = _textured(7)
    small = cv2.resize(img, (32, 32), interpolation=cv2.INTER_AREA).astype(np.float64)
    d = dct_matrix(32)
    coeffs = d @ small @ d.T  # DCT-II 2D orthonormée
    block = coeffs[1:9, 1:9].ravel()
    med = np.median(block)
    expected = 0
    for k, c in enumerate(block):  # bit k=0 = poids fort
        if c > med:
            expected |= 1 << (63 - k)
    assert phash_gray(img)[0] == expected


def test_uniform_image_hash_and_informativeness():
    flat = np.full((64, 64), 120, np.uint8)
    value, info = phash_gray(flat)
    assert value == 0  # tous les coefficients AC sont nuls -> aucun bit > médiane
    assert info < 0.5
    assert phash_gray(_textured(3))[1] > 10


def test_brightness_invariance_and_robustness():
    img = _textured(11)
    h0 = phash_gray(img)[0]
    darker = (img.astype(np.float32) * 0.25).astype(np.uint8)  # scène sombre
    assert hamming(h0, phash_gray(darker)[0]) <= 4
    noisy = np.clip(img.astype(np.int16) + np.random.default_rng(0).integers(-6, 7, img.shape), 0, 255).astype(np.uint8)
    assert hamming(h0, phash_gray(noisy)[0]) <= 6
    resized = cv2.resize(cv2.resize(img, (40, 40)), (64, 64))
    assert hamming(h0, phash_gray(resized)[0]) <= 6
    other = phash_gray(_textured(999))[0]
    assert hamming(h0, other) > 12  # deux images différentes restent éloignées


def test_hamming_distance():
    assert hamming(0, 0) == 0
    assert hamming(0, (1 << 64) - 1) == 64
    assert hamming(0b1011, 0b0001) == 2
    rng = random.Random(5)
    values = [rng.getrandbits(64) for _ in range(200)]
    arr = np.array(values, dtype=np.uint64)
    q = values[17]
    assert list(hamming_many(arr, q)) == [hamming(v, q) for v in values]


@pytest.mark.parametrize("value", [0, 1, 2**63 - 1, 2**63, 2**64 - 2, 2**64 - 1, 0x0123456789ABCDEF])
def test_blob_roundtrip_extremes(value):
    blob = to_blob(value)
    assert len(blob) == 8
    assert from_blob(blob) == value
    assert int(blobs_to_array([blob])[0]) == value
    assert len(to_hex(value)) == 16 and int(to_hex(value), 16) == value


def test_blob_rejects_out_of_range():
    with pytest.raises(ValueError):
        to_blob(-1)
    with pytest.raises(ValueError):
        to_blob(2**64)
    with pytest.raises(ValueError):
        from_blob(b"1234")


def test_bands_reconstruct_hash():
    rng = random.Random(9)
    for _ in range(100):
        v = rng.getrandbits(64)
        b = bands(v)
        assert len(b) == 4 and all(0 <= x <= 0xFFFF for x in b)
        assert (b[0] << 48 | b[1] << 32 | b[2] << 16 | b[3]) == v


def test_band_probes():
    assert band_probes(0x1234, 0) == [0x1234]
    probes = band_probes(0x1234, 1)
    assert len(probes) == 17 and len(set(probes)) == 17
    assert all(hamming(p, 0x1234) <= 1 for p in probes)
