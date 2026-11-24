# Résultats mesurés — MovieShazam

Toutes les valeurs ci-dessous ont été **mesurées** (aucune n'est estimée ou simulée) le 15/11/2026 sur la
machine de développement. Fichiers bruts : `eval_*.json`, `calibration.json`.

## Machine de mesure

| | |
|---|---|
| Processeur | AMD Ryzen 5 5600H (6 cœurs physiques, 12 logiques) |
| Mémoire | **8 Go, une seule barrette (un seul canal mémoire)**, ~1 Go libre pendant les mesures |
| Système | Windows 11, secteur branché |
| Logiciels | Python 3.12.10, FFmpeg 9.0.2, SQLite 3.49.1 |
| Réglages moteur | `phash-v1@2fps`, FFmpeg `-threads 1` par processus, segments de 10 à 180 s (≥ 8 par film) |

> Remarque : le cahier des charges vise un PC de 16 Go. La machine disponible n'en a que 8 (un seul canal).

## 1. Exactitude de la reconnaissance (corpus de démonstration)

Corpus : 4 films indexés (1280×720, 25 img/s, 345 s au total) + 2 films jamais indexés ; 28 clips (12 validation, 16 test).
Seuils calibrés **uniquement sur la validation** (`calibration.json`) : pire vraie correspondance 0,830, meilleur
candidat faux 0,146 → seuil d'acceptation 0,50 ; plus grand rapport faux/vrai 0,05 → ratio d'ambiguïté 0,85.

| Split | Méthode | Corrects | Erreur d'horodatage moyenne | Erreur max | Recherche moyenne* |
|---|---|---|---|---|---|
| validation | bandes | **12/12** | 0 ms | 0 ms | 339 ms |
| validation | exhaustive | **12/12** | 0 ms | 0 ms | 303 ms |
| test | bandes | **16/16** | 8,3 ms | 50 ms | 369 ms |
| test | exhaustive | **16/16** | 8,3 ms | 50 ms | 331 ms |

\* temps complet côté moteur : décodage du clip + empreintes + candidats + alignement (le décodage du clip domine).
Détail clip par clip : `eval_validation.md`, `eval_test.md`. Tolérance demandée : ±1000 ms.

Cas couverts : début / milieu / fin de film, franchissement d'une et de deux frontières de segment, début non
aligné sur la seconde, recompression CRF 33, 640 px, 480 px + 15 img/s + CRF 30, 25→30 img/s, scène sombre,
logo commun à tous les films, scène répétée dans un film (→ ambigu), passage partagé entre deux films (→ ambigu ;
avec 7 s de contexte propre → bon film), 5 clips de films absents (→ aucun résultat fiable).

## 2. Recherche : index par bandes vs recherche exhaustive

Sur les 28 clips : **28/28 verdicts corrects avec les deux méthodes**, verdicts identiques.

## 3. Réactivité et robustesse (tests automatisés)

* Latence de `GET /api/jobs/{id}` pendant l'indexation de deux films sur 2 processus (serveur Uvicorn réel, version
  sans asyncio où chaque requête est servie par un thread) : médiane 4,2 ms, p95 7,0 ms, max 10,1 ms (30 mesures) ;
  `GET /api/stats` : médiane 8,0 ms. (Version précédente à base d'asyncio : moyenne 2,5 ms, max 3,5 ms.)
* 73 tests pytest : 71 unitaires/intégration + 2 de bout en bout (navigateur Edge → Next.js → FastAPI → SQLite), tous verts.
* Arrêt gracieux vérifié : aucun processus du pool ni FFmpeg orphelin.
