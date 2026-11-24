from __future__ import annotations

import cv2
import numpy as np

ALGORITHM_NAME = "phash-v1"
HASH_BITS = 64
PHASH_SIZE = 32
BLOCK = slice(1, 9)
N_BANDS = 4
BAND_BITS = 16
_MASK64 = (1 << 64) - 1


def phash_gray(frame: np.ndarray) -> tuple[int, float]:
    if frame.ndim != 2:
        raise ValueError("phash_gray attend une image en niveaux de gris (2D)")
    small = cv2.resize(frame, (PHASH_SIZE, PHASH_SIZE), interpolation=cv2.INTER_AREA).astype(np.float64)
    informativeness = float(small.std())
    coeffs = cv2.dct(small)
    block = coeffs[BLOCK, BLOCK]
    bits = (block > np.median(block)).ravel()
    value = int.from_bytes(np.packbits(bits).tobytes(), "big")
    return value, informativeness


def dct_matrix(n: int = PHASH_SIZE) -> np.ndarray:
    k = np.arange(n)[:, None]
    i = np.arange(n)[None, :]
    m = np.cos(np.pi * (2 * i + 1) * k / (2 * n)) * np.sqrt(2.0 / n)
    m[0, :] = np.sqrt(1.0 / n)
    return m


def hamming(a: int, b: int) -> int:
    return ((a ^ b) & _MASK64).bit_count()


def hamming_many(hashes: np.ndarray, query: int) -> np.ndarray:
    return np.bitwise_count(np.bitwise_xor(hashes, np.uint64(query))).astype(np.int16)


def to_blob(value: int) -> bytes:
    if not 0 <= value <= _MASK64:
        raise ValueError("une empreinte doit être un entier non signé de 64 bits")
    return value.to_bytes(8, "big")


def from_blob(blob: bytes) -> int:
    if len(blob) != 8:
        raise ValueError("une empreinte stockée doit faire exactement 8 octets")
    return int.from_bytes(blob, "big")


def blobs_to_array(blobs: list[bytes]) -> np.ndarray:
    if not blobs:
        return np.zeros(0, dtype=np.uint64)
    return np.frombuffer(b"".join(blobs), dtype=">u8").astype(np.uint64)


def to_hex(value: int) -> str:
    return f"{value:016x}"


def bands(value: int) -> list[int]:
    return [(value >> (HASH_BITS - BAND_BITS * (i + 1))) & 0xFFFF for i in range(N_BANDS)]


def band_probes(band_value: int, radius: int) -> list[int]:
    probes = [band_value]
    if radius >= 1:
        probes += [band_value ^ (1 << b) for b in range(BAND_BITS)]
    return probes
