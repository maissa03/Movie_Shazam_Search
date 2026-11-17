# Plan de présentation — 12 slides (≈ 12 min + démonstration)

Tous les chiffres viennent de `docs/results/RESULTATS.md` (mesurés sur la machine de développement). Aucun n'est inventé.

---

### 1. Titre — MovieShazam
*Retrouver le film et l'instant exact d'un extrait vidéo, entièrement sur un PC.*
**Notes :** se présenter, annoncer : problème, algorithme, parallélisme local, résultats, limites.

### 2. Le problème
« J'ai 10 s d'une vidéo : de quel film, à quel instant ? » — bibliothèque locale, clip réencodé, début arbitraire.
Pourquoi c'est difficile : jamais identique octet par octet, images génériques (noir, logo, générique), volume
(50 films × 2 h × 2 img/s = 720 000 empreintes).
**Notes :** montrer 3 vignettes d'extraits (connu, recompressé, inconnu).

### 3. Utilité et périmètre
Origine d'un extrait, exploration d'archives, détection de passages réutilisés dans une collection connue.
Hors périmètre V1 : « tous les films du monde », écran filmé, gros recadrage.
**Notes :** insister sur l'honnêteté : refus clair et résultat ambigu plutôt qu'une invention.

### 4. Empreinte perceptuelle : le pHash
Image → 64×64 gris (FFmpeg) → 32×32 → DCT → 64 basses fréquences → bit = coefficient > médiane → 64 bits.
Distance de Hamming = popcount(a XOR b). Robuste à la recompression, au redimensionnement, à la luminosité.
**Notes :** contraste avec SHA-256 (un pixel change tout). Mentionner le BLOB 8 octets (entier SQLite signé).

### 5. Alignement temporel
Vote sur le décalage `t_film − t_clip` ; vérification de l'ordre temporel ; score = similarité × couverture ×
stabilité ; décision correspondance / ambigu / aucune. Clip échantillonné 10 img/s contre 2 pour l'index.
Seuils calibrés sur la validation (pire vrai 0,83, meilleur faux 0,15 → seuil 0,50).
**Notes :** « une image ressemblante ne suffit jamais » ; anecdote du biais de −200 ms trouvé et corrigé (filtre fps de FFmpeg).

### 6. Architecture locale
Navigateur ⇄ HTTP 127.0.0.1 ⇄ FastAPI (un seul processus) : routes servies par des threads + travaux de fond
en threads + pool de processus + threads E/S + écrivain SQLite unique. Aucun réseau entre machines, pas d'asyncio.
**Notes :** expliquer pourquoi le polling HTTP suffit (pas de WebSocket) ; processus pour calculer, threads pour attendre.

### 7. multiprocessing (chapitre 2)
Une tâche = un segment ; `ProcessPoolExecutor` en `spawn`, créé une fois ; fonction de module, résultats compacts ;
`cv2.setNumThreads(1)` et FFmpeg `-threads 1` contre la sursouscription ; préchauffage par `Barrier`, arrêt par `Event`.
Preuve : PID distincts, empreintes identiques au séquentiel.
**Notes :** montrer la carte des segments colorée par PID.

### 8. threading (chapitre 1) : E/S et écrivain unique
`ThreadPoolExecutor(2)` pour copie + SHA-256 + ffprobe + vignettes ; producteur–consommateur `queue.Queue` bornée
vers **un seul thread écrivain SQLite** (transactions, sentinelle, non daemon) ; `Timer` comme watchdog FFmpeg.
**Notes :** test de course : 8 threads sans verrou perdent des incréments ; avec l'écrivain unique, 0 perte.

### 9. threading (chapitre 1) : requêtes et travaux de fond
Routes `def` exécutées par les threads de FastAPI, `202 Accepted` + id de job, polling ; file `queue.Queue` bornée
des indexations consommée par un thread (sentinelle d'arrêt) ; `ThreadPoolExecutor(2)` pour les recherches ;
`Condition` pour l'exclusivité du benchmark ; `Event` par job ; `Lock` sur la table des jobs (lue par le polling,
écrite par les traitements). API à ~4 ms (médiane) pendant une indexation.
**Notes :** pourquoi pas asyncio : peu de connexions simultanées, des threads suffisent et sont plus simples ;
anecdote de la course « ancien état écrit après le nouveau », évitée en mettant l'écriture en file sous le verrou.

### 10. SQLite et index de recherche
Tables movies / segments / fingerprints / fingerprint_bands / jobs / searches / benchmarks ; WAL ; contraintes
d'unicité (pas de doublon, pas de mélange de versions) ; index incrémental et reprise.
Index par bandes 4×16 bits + multi-probe : rappel garanti pour d ≤ 7 ; 500 690 empreintes : 451 ms contre 1 338 ms
en exhaustive, mêmes verdicts.
**Notes :** principe des tiroirs ; montrer la courbe de rappel.

### 11. Benchmarks et tests
Indexation 720p : ×1,39 (2 proc.), ×1,32 (4 proc.) ; même corpus 480×270 : ×1,77 et ×2,43 ; FFmpeg seul plafonne
aussi à 720p (×1,13 avec 4 copies) → limite matérielle (8 Go, un seul canal mémoire). Reconnaissance : 12/12
validation, 16/16 test, erreur moyenne 8 ms. 73 tests automatisés dont 2 de bout en bout.
**Notes :** expliquer pourquoi plus de processus peut ralentir (Amdahl, démarrage, contention mémoire, sursouscription).

### 12. Limites et conclusion
Limites : recadrage/miroir/écran filmé, score heuristique, corpus de validation synthétique, mesures à chaud,
8 Go de RAM. Pistes : audio, multi-probe adaptatif, index en mémoire, vérification fine à 10 img/s.
Conclusion : un moteur réellement fonctionnel et vérifiable, et une application raisonnée — pas décorative —
de `multiprocessing` et de `threading` sur une seule machine.
**Notes :** enchaîner sur la démonstration (`docs/DEMO_SOUTENANCE.md`).
