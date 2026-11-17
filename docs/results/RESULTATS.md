# Résultats mesurés — MovieShazam

Toutes les valeurs ci-dessous ont été **mesurées** (aucune n'est estimée ou simulée) le 15/11/2026 sur la
machine de développement. Fichiers bruts : `benchmark_latest.json`, `benchmark_*.csv`, `eval_*.json`,
`calibration.json`. Pour les reproduire : `python scripts/evaluate.py …` et `python scripts/run_benchmark.py …`.

## Machine de mesure

| | |
|---|---|
| Processeur | AMD Ryzen 5 5600H (6 cœurs physiques, 12 logiques) |
| Mémoire | **8 Go, une seule barrette (un seul canal mémoire)**, ~1 Go libre pendant les mesures |
| Système | Windows 11, secteur branché |
| Logiciels | Python 3.12.10, FFmpeg 9.0.2, SQLite 3.49.1 |
| Réglages moteur | `phash-v1@2fps`, FFmpeg `-threads 1` par processus, segments de 10 à 180 s (≥ 8 par film) |

> Remarque : le cahier des charges vise un PC de 16 Go. La machine disponible n'en a que 8 (un seul canal),
> ce qui influence directement les résultats de parallélisme ci-dessous.

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

## 2. Indexation : séquentiel vs N processus (médiane de 3 répétitions, ordre des modes alterné)

Même corpus, mêmes 30 segments, même fonction `analyze_segment`. Toutes les exécutions à chaud (cache disque).

### 2.1 Corpus de démonstration 1280×720 (345 s de vidéo)

| Mode | Temps | Speedup | Efficacité | Images décodées/s | Empreintes/s | Écriture SQLite | Cœurs occupés | RAM pic | Empreintes identiques | PID |
|---|---|---|---|---|---|---|---|---|---|---|
| Séquentiel | 14,59 s | ×1,00 | 100 % | 47 | 47,3 | 76 ms | 1,6 | 123 Mo | référence | 1 (processus courant) |
| 1 processus | 12,99 s | ×1,12 | 112 % | 53 | 53,1 | 79 ms | 1,3 | 196 Mo | oui | 1 distinct |
| 2 processus | 10,52 s | **×1,39** | 69 % | 66 | 65,6 | 80 ms | 2,3 | 311 Mo | oui | 2 distincts |
| 4 processus | 11,02 s | ×1,32 | 33 % | 63 | 62,6 | 83 ms | 3,1 | 542 Mo | oui | 4 distincts |

### 2.2 Le même corpus en 480×270

| Mode | Temps | Speedup | Efficacité | Images/s | RAM pic | Empreintes identiques |
|---|---|---|---|---|---|---|
| Séquentiel | 3,56 s | ×1,00 | 100 % | 194 | 115 Mo | référence |
| 1 processus | 3,68 s | ×0,97 | 97 % | 188 | 187 Mo | oui |
| 2 processus | 2,00 s | **×1,77** | 89 % | 344 | 294 Mo | oui |
| 4 processus | 1,46 s | **×2,43** | 61 % | 472 | 503 Mo | oui |

### 2.3 Expérience de contrôle : décodage FFmpeg brut (N copies simultanées du même film, débit relatif)

| Résolution | 1 processus | 2 processus | 4 processus |
|---|---|---|---|
| 480×270 | ×1,00 | ×1,72 | ×2,61 |
| 1280×720 | ×1,00 | ×1,11 | ×1,13 |
| 1920×1080 | ×1,00 | ×1,40 | ×1,66 |

Mesures antérieures du même type pendant le développement : 720p ×1,10–1,25 à 2 processus ; 1080p entre ×0,70 et
×1,66 à 4 processus selon l'exécution (forte variabilité à haute résolution sur cette machine).

### 2.4 Interprétation (honnête)

* Le parallélisme **fonctionne** : PID distincts, empreintes strictement identiques au séquentiel, et sur un travail
  limité par le CPU (480×270) le speedup atteint ×1,77 (2 processus) et ×2,43 (4 processus).
* À 720p, le gain plafonne à ≈ ×1,4 et 4 processus ne font pas mieux que 2. Le même plafond apparaît avec FFmpeg
  seul, **sans aucun code Python** : la limite n'est donc ni le GIL, ni l'IPC, ni SQLite, mais le décodage HD
  lui-même lorsqu'il est exécuté en plusieurs exemplaires. Explication la plus cohérente avec les mesures :
  contention sur le sous-système mémoire (cache L3 partagé et bande passante d'**un seul canal mémoire**, RAM
  presque pleine). Nous ne disposons pas de compteurs matériels pour le prouver formellement.
* Le surcoût de la parallélisation est visible : démarrage des processus (mesuré à part, non inclus), +70 à
  +100 Mo de RAM par processus, efficacité qui baisse avec N. L'écriture SQLite (≈ 80 ms pour 690 empreintes) est
  négligeable ici.
* Conclusion pratique : `PROCESS_WORKERS=2` par défaut ; sur une machine à double canal ou pour des vidéos de
  plus faible résolution, 4 processus deviennent intéressants. La page « Performances » permet de le mesurer.

## 3. Recherche : index par bandes vs recherche exhaustive

### 3.1 Exactitude
Sur les 28 clips : **28/28 verdicts corrects avec les deux méthodes**, verdicts identiques.

### 3.2 Rappel du filtre par bandes (150 empreintes réelles, d bits inversés au hasard)

| d (bits modifiés) | 0 | 2 | 4 | 6 | 8 | 10 | 12 |
|---|---|---|---|---|---|---|---|
| 4 bandes exactes | 1,00 | 1,00 | 0,86 | 0,58 | 0,33 | 0,21 | 0,09 |
| multi-probe 1 bit | 1,00 | 1,00 | 1,00 | 1,00 | 0,93 | 0,76 | 0,47 |

Garanties théoriques vérifiées : rappel 100 % pour d ≤ 3 (4 bandes) et d ≤ 7 (multi-probe).

### 3.3 Passage à l'échelle (16 clips test, médiane ; empreintes distractrices aléatoires dans une base jetable)

| Taille de l'index | Bandes | Exhaustive | Gain | Exactitude (bandes / exhaustive) |
|---|---|---|---|---|
| 690 | 12,4 ms | 4,6 ms | ×0,4 | 16/16 / 16/16 |
| 50 690 | 48,8 ms | 139,2 ms | ×2,9 | 16/16 / 16/16 |
| 200 690 | 185,1 ms | 475,8 ms | ×2,6 | 16/16 / 16/16 |
| 500 690 | 450,6 ms | 1 338,0 ms | ×3,0 | 16/16 / 16/16 |

(Temps « candidats + alignement », sans le décodage du clip.) Sur un tout petit index, l'exhaustive est plus rapide
(coût fixe des sondes SQL) ; dès quelques dizaines de milliers d'empreintes, l'index par bandes l'emporte sans perte
d'exactitude. Son coût croît encore avec la taille car chaque sonde rencontre ~N/65 536 collisions aléatoires,
mais il ne lit qu'environ 1–2 % de l'index par requête.

## 4. Réactivité et robustesse (tests automatisés)

* Latence de `GET /api/jobs/{id}` pendant l'indexation de deux films sur 2 processus (serveur Uvicorn réel, version
  sans asyncio où chaque requête est servie par un thread) : médiane 4,2 ms, p95 7,0 ms, max 10,1 ms (30 mesures) ;
  `GET /api/stats` : médiane 8,0 ms. (Version précédente à base d'asyncio : moyenne 2,5 ms, max 3,5 ms.)
* 73 tests pytest : 71 unitaires/intégration + 2 de bout en bout (navigateur Edge → Next.js → FastAPI → SQLite), tous verts.
* Arrêt gracieux vérifié : aucun processus du pool ni FFmpeg orphelin.
