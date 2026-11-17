"""pHash DCT 64 bits, distance de Hamming et conversions de stockage.

Spécification exacte de ``phash-v1`` (identique à l'indexation ET à la recherche) :

1. Entrée : image en niveaux de gris 64x64 (luminance) produite par FFmpeg
   (``scale=64:64:flags=area,format=gray``).
2. Réduction OpenCV en 32x32 avec ``INTER_AREA`` (moyenne 2x2) puis conversion ``float64``.
3. DCT-II 2D orthonormée (``cv2.dct``) -> matrice 32x32 de coefficients ``C[u, v]``
   (u = fréquence verticale, v = fréquence horizontale).
4. **Masque de 64 coefficients** : le bloc ``C[1:9, 1:9]`` (lignes 1..8, colonnes 1..8).
   On ignore la ligne 0 et la colonne 0, donc le coefficient DC ``C[0,0]`` (luminosité
   moyenne) n'entre jamais dans l'empreinte : c'est la convention de Zauner (pHash.org).
5. ``m`` = médiane de ces 64 coefficients (``numpy.median``, moyenne des 32e et 33e valeurs).
6. Bit ``k`` (k = 8*(u-1) + (v-1), ordre ligne par ligne) = 1 si ``C[u,v] > m`` (strict), sinon 0.
7. Entier 64 bits **non signé** : le bit k=0 est le bit de poids fort
   (``hash = sum(bit_k << (63 - k))``).

Propriétés utiles :
  * invariant à un changement global de luminosité/contraste (DC exclu, seuil = médiane) ;
  * robuste à la recompression et au redimensionnement (basses fréquences uniquement) ;
  * une image uniforme donne des coefficients nuls -> hash 0 : on la marque donc comme
    **peu informative** grâce à ``informativeness`` (écart-type de la vignette 32x32).

Stockage : BLOB de 8 octets **big-endian** (aucune perte de bit, contrairement à un
INTEGER SQLite signé limité à 2^63-1) ; affichage en hexadécimal sur 16 caractères.
"""

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
    """Calcule ``(hash64, informativeness)`` pour une image en niveaux de gris.

    ``frame`` : tableau 2D ``uint8`` (normalement 64x64 ; toute taille est acceptée).
    """
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
    """Matrice DCT-II orthonormée (référence NumPy pure, utilisée par les tests)."""
    k = np.arange(n)[:, None]
    i = np.arange(n)[None, :]
    m = np.cos(np.pi * (2 * i + 1) * k / (2 * n)) * np.sqrt(2.0 / n)
    m[0, :] = np.sqrt(1.0 / n)
    return m


def hamming(a: int, b: int) -> int:
    """Distance de Hamming entre deux empreintes 64 bits (nombre de bits différents)."""
    return ((a ^ b) & _MASK64).bit_count()


def hamming_many(hashes: np.ndarray, query: int) -> np.ndarray:
    """Distances de Hamming vectorisées entre ``query`` et un tableau ``uint64``."""
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
    """Convertit une liste de BLOB big-endian en tableau ``uint64`` natif."""
    if not blobs:
        return np.zeros(0, dtype=np.uint64)
    return np.frombuffer(b"".join(blobs), dtype=">u8").astype(np.uint64)


def to_hex(value: int) -> str:
    return f"{value:016x}"


def bands(value: int) -> list[int]:
    """Découpe l'empreinte en 4 bandes de 16 bits (bande 0 = bits de poids fort)."""
    return [(value >> (HASH_BITS - BAND_BITS * (i + 1))) & 0xFFFF for i in range(N_BANDS)]


def band_probes(band_value: int, radius: int) -> list[int]:
    """Valeurs de bande à sonder : la valeur exacte (+ ses 16 voisines à 1 bit si radius>=1).

    Multi-probe : avec 4 bandes, deux empreintes à distance <= 3 partagent forcément
    une bande identique (principe des tiroirs) ; en sondant aussi les voisins à 1 bit,
    toute paire à distance <= 7 est garantie d'être retrouvée.
    """
    probes = [band_value]
    if radius >= 1:
        probes += [band_value ^ (1 << b) for b in range(BAND_BITS)]
    return probes
