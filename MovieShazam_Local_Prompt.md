# PROMPT — MOVIESHAZAM

> **Mission :** développer, tester et livrer une application réellement fonctionnelle qui retrouve **le titre d'un film et l'horodatage d'une scène** à partir d'un extrait vidéo de 5 à 20 secondes, parmi les films **préalablement ajoutés et indexés localement**. Le moteur est écrit en **Python**, l'interface en **Next.js / TypeScript**. Le projet sert aussi de démonstration sérieuse de **programmation parallèle et concurrente sur un seul ordinateur**.
>
> **Exigence absolue : TOUT fonctionne sur un unique PC Windows de 16 Go de RAM, sans GPU ni service cloud.** Aucun worker sur un autre PC, aucune architecture distribuée, aucun orchestrateur TCP, aucune socket TCP/UDP programmée manuellement, aucun cluster, aucun heartbeat réseau. Ne propose pas ces éléments et ne les implémente pas, même en option.

---

## 0. Ton rôle et les règles non négociables

Agis comme un ingénieur logiciel senior spécialisé en Python, traitement vidéo, algorithmique, concurrence et développement d'applications web. Tu dois **écrire le code complet, l'exécuter, le tester et le corriger** — pas seulement fournir une architecture, des maquettes ou du pseudo-code.

1. L'application s'appelle **MovieShazam** (nom provisoire ; aucune affiliation avec Shazam).
2. Backend : **Python 3.11 ou 3.12 + FastAPI**. Traitement visuel : **FFmpeg / ffprobe, NumPy et OpenCV**. Persistance : **SQLite**. Frontend : **Next.js App Router + TypeScript + Tailwind CSS**.
3. La totalité du traitement des films et de la recherche doit être réalisée par le backend Python. Next.js n'effectue ni décodage massif, ni pHash, ni calcul CPU des empreintes.
4. **Un seul PC.** Le navigateur et le backend communiquent seulement via HTTP sur `localhost` (`127.0.0.1`), comme toute application web locale. Pour afficher la progression, utilise de préférence du **polling HTTP** simple ; pas de WebSocket obligatoire et **pas de protocole réseau personnalisé**.
5. **`multiprocessing` est requis pour le CPU-bound**, `threading` seulement pour les E/S bloquantes pertinentes, `asyncio` pour coordonner les travaux et garder l'API réactive. Sélectionner les primitives selon le besoin concret, pas pour cocher une liste.
6. Prévoir un mode **séquentiel** et un mode **parallèle local**, exécutant la **même logique métier** pour comparer honnêtement les résultats et les performances.
7. Aucun GPU, aucune IA payante, aucun LLM et **aucun entraînement de modèle** : utiliser des **empreintes perceptuelles visuelles** (pHash) et une **vérification de cohérence temporelle**.
8. Ne jamais charger un film entier ni toutes les images décodées en RAM. Décoder **en flux et par segments bornés** ; RAM maîtrisée sur 16 Go, même avec plusieurs films.
9. Vraies données partout : jamais de film reconnu en dur, de barre de progression fictive, de pourcentage de confiance inventé ou de benchmark simulé.
10. Générer localement des vidéos et extraits de test reproductibles. Aucun téléchargement de film commercial ou récupération externe obligatoire.
11. Priorité stricte : **moteur fonctionnel → tests → parallélisation → API → interface → benchmarks**. Une interface spectaculaire ne compense pas un algorithme qui ne marche pas.
12. Pas de Docker, Redis, Celery, Kafka, Kubernetes, microservices, sockets custom ou tableaux de bord de « machines connectées » : c'est un **projet local simple à installer et techniquement sérieux**.
13. L'interface, la documentation utilisateur et la présentation académique sont rédigées en **français** ; noms de modules, variables et schémas techniques peuvent être en anglais.
14. Si une fonctionnalité avancée n'est pas finie, le dire explicitement et ne pas la présenter comme fonctionnelle. Ne laisse pas de TODO critiques ou de boutons inutilisables.

**Résultat attendu :** un dépôt complet, une seule procédure de démarrage local, un moteur réellement utilisable, une belle interface, des tests et une documentation pédagogique adaptée à la soutenance.

---

## 1. Contexte du projet universitaire — respecter le cours

Le module est **Systèmes distribués et parallèles**, enseigné en 2026–2027 par **Dr. Mounira Ebdelli** à l'ISAMM. Les supports fournis avec le projet sont :

- `Projet-Systèmes.pdf` : cahier des charges et barème.
- `SystèmesDP-intro.pdf` : systèmes parallèles/distribués, architectures et formes de programmation parallèle.
- `SystèmesDP-chap1-part1.pdf` : threads, création et exécution.
- `SystèmesDP-chap1-part2.pdf` : pools de threads, synchronisation, `Lock`, `RLock`, `Condition`, `Semaphore`, situations de compétition.
- `SystèmesDP-chap2.pdf` : `multiprocessing.Process`, `Pool`, `Queue`, `Pipe`, état partagé, synchronisation et comparaison avec les threads.
- `SystèmesDP-chap4.pdf` : sockets réseau ; **chapitre non utilisé**, car l'utilisateur veut expressément une solution 100 % locale sans communication personnalisée par sockets.

Le sommaire mentionne également un chapitre 3 sur `asyncio`, mais son support PDF détaillé n'a pas été fourni ; toute recommandation détaillée sur cette API vient de la conception technique et ne doit pas être attribuée abusivement à l'enseignante.

Le cahier des charges évalue : **originalité du sujet (2 points), travail/code (12 points) et présentation (6 points)**. Dans la partie code, la **qualité du code pèse 60 %**. Faire des choix de concurrence explicables, produire des résultats justes et des mesures reproductibles est donc plus important qu'accumuler des technologies.

**Point pédagogique à montrer :** il est possible de réaliser du parallélisme multicœur, du multithreading et de la programmation asynchrone **sur une seule machine**. Le module propose plusieurs outils, mais n'exige pas que le projet utilise toutes les techniques. Les sockets ne sont pas nécessaires pour notre moteur local.

---

## 2. Problème concret, portée et valeur de MovieShazam

**Question utilisateur :** « J'ai 10 secondes d'une vidéo ; dans ma bibliothèque locale de films, de quel film viennent ces 10 secondes et à quel instant exact ? »

**Scénario :** j'indexe 10, puis 50 films que je possède légalement. Je donne un clip MP4 de 10 secondes, extrait d'un de ces films à `00:42:13`. Le système analyse ce clip, identifie le bon film et renvoie un début voisin de `00:42:13`, avec une tolérance clairement définie et un **score de similarité calculé**.

**Utilités :** recherche d'origine d'un extrait, exploration d'archives vidéo, identification d'une scène, détection de passages réutilisés au sein d'une collection connue.

**Dans la V1 :**

- Reconnaître un extrait **présent dans un film déjà indexé**.
- Reconnaître la même scène après **recompression raisonnable**, conversion de codec ou réduction de résolution dans les limites validées par les tests.
- Trouver un passage placé **n'importe où** dans le film, pas seulement au début.
- Fournir un résultat « **Aucune correspondance suffisamment fiable** » lorsque nécessaire ; signaler les ambiguïtés.

**Hors périmètre V1 :** trouver n'importe quel film du monde sans bibliothèque ; identifier systématiquement un écran filmé au téléphone ; garantir la reconnaissance après gros recadrage, fortes superpositions ou montage temporel réordonné ; dépendre d'une API extérieure. Ces cas peuvent figurer dans les limites, **pas** dans les promesses.

**Données :** vidéos originales créées par l'équipe, séquences libres de droits ou films légalement utilisables pour les essais. Ne pas placer de films protégés dans le dépôt.

---

## 3. Fonctionnalités utilisateur réellement implémentées

### A. Bibliothèque locale

- Import d'un film vidéo depuis le navigateur vers le stockage local de l'application ; validation du fichier avec `ffprobe`.
- Titre, année facultative, durée, résolution, format, taille, date, miniature et état d'indexation.
- Liste des films avec recherche textuelle, tri, détail, suppression et réindexation.
- **Indexation persistante et incrémentale** : un fichier inchangé et déjà indexé avec la même version d'algorithme ne doit pas être recalculé inutilement.
- États : `pending`, `indexing`, `indexed`, `failed`, `cancelled` ; progression **réelle** (`segments_completed / segments_total`).
- Suppression sûre et cohérente des empreintes, des index et des caches du film ; empêcher une suppression destructrice d'un fichier extérieur au stockage de l'application.

### B. Trouver un film

- Déposer un clip vidéo (drag-and-drop), le prévisualiser et cliquer sur **« Identifier le film »**.
- Voir les étapes réelles : validation → empreintes du clip → recherche des candidats → alignement temporel → résultat.
- Afficher titre, vignette, début et fin estimés `HH:MM:SS`, score expliqué, couverture temporelle et éventuellement **Top 3** candidats.
- Un lecteur ou une timeline doit permettre de comprendre visuellement où se trouve le passage dans le film ; ne jamais fournir au navigateur des chemins absolus arbitraires du PC.
- Gérer absence de correspondance et résultats ambigus de manière honnête.

### C. Traitements locaux

- Page **Traitements** : jobs d'indexation/recherche, film concerné, progrès, durée, statut, erreur éventuelle, annulation et historique.
- Afficher le **nombre de processus CPU locaux réellement utilisés**, le nombre de segments terminés et les empreintes calculées.
- **Pas** de page Cluster/Workers distants, aucune IP de worker, aucun heartbeat, aucun transfert vers un autre ordinateur.

### D. Benchmarks

- Comparer le temps réel d'indexation du **même corpus** en séquentiel, 2 processus et, si le matériel le permet, 4 processus.
- Mesurer temps total, segments traités, frames/s, fingerprints/s, consommation mémoire estimée, CPU lorsque mesurable, gain (_speedup_) et efficacité.
- Comparer aussi recherche exhaustive de référence et recherche avec index optimisé sur un petit corpus contrôlé.
- Afficher des graphiques issus de vraies mesures et permettre l'export CSV/JSON.

---

## 4. Architecture locale, volontairement simple

```text
                         UTILISATEUR
                 Next.js / TypeScript
         Bibliothèque | Recherche | Jobs | Benchmark
                            |
                     HTTP localhost
                            |
              +-------------v--------------+
              |      FASTAPI / PYTHON       |
              |  API + asyncio Job Manager  |
              |  files, validation, events  |
              +------+---------------------+
                     |
           +---------+------------------+
           |                            |
 +---------v-----------+     +----------v----------+
 | ProcessPoolExecutor |     | ThreadPoolExecutor  |
 | CPU / traitements   |     | I/O locales         |
 | FFmpeg + pHash       |     | fichiers / metadata |
 | Processus 1..N      |     | uploads / miniatures|
 +---------+-----------+     +----------+----------+
           |                            |
           +-------------+--------------+
                         |
             Agrégation locale de résultats
                         |
                   SQLite + index
                         |
                 Moteur de recherche
          candidats + alignement temporel
```

**Ce schéma représente des composants du même PC.** Le pool de processus fournit du calcul parallèle réel sur plusieurs cœurs. Les threads s'occupent des E/S bloquantes appropriées. L'event loop `asyncio` orchestre sans faire le calcul lourd elle-même. **Il n'existe aucun orchestrateur TCP et aucun processus worker externe à lancer manuellement.**

Le frontend contacte `http://127.0.0.1:8000`, tandis que Next.js est accessible sur `http://localhost:3000`. Cette communication HTTP locale n'est **pas** un système de calcul distribué : ce sont les deux composants normaux d'une application web sur le même PC.

### Deux modes d'exécution du même moteur

1. **Séquentiel** : segments traités les uns après les autres dans un chemin de référence simple.
2. **Parallèle local** : segments indépendants confiés à un `ProcessPoolExecutor` persistant ; le nombre de processus est configurable.

Les deux modes doivent utiliser **la même fonction d'analyse de segment, le même pHash et la même version d'index**, afin de pouvoir comparer résultats et performances sans biais.

---

## 5. Moteur de fingerprinting vidéo : le cœur algorithmique

### 5.1 Préparer et indexer un film

Pour chaque fichier vidéo :

1. Utiliser `ffprobe` pour obtenir durée réelle, résolution, codec, fréquence d'image et présence de flux vidéo.
2. Diviser le film en **segments bornés**, initialement **180 secondes** (paramétrable). Les segments représentent des unités de travail CPU indépendantes, pas des fichiers vidéo à sauvegarder.
3. Extraire initialement **2 images par seconde**, avec un décodage FFmpeg **en flux**, réduction de résolution et niveaux de gris. Traiter image par image (ou mini-lots), **jamais un film entier en mémoire**.
4. Calculer un **pHash DCT de 64 bits** déterministe pour chaque image. Enregistrer `(movie_id, timestamp_ms_global, fingerprint, fingerprint_version, segment_id, informativeness)`.
5. Conserver un horodatage **global du film**, même si un worker décode seulement une portion. Tester les cas où la requête commence à `42:13.3`, les vidéos à FPS variable et les frontières entre segments.
6. Éviter les trous ou doublons non maîtrisés aux frontières : éventuellement léger chevauchement paramétrable puis déduplication `(movie_id, timestamp_ms, version)`.
7. Réduire l'influence des images presque uniformes, logos et génériques répétitifs sans exclure injustement toute scène sombre.
8. Retourner des **résultats compacts** (hashes et timestamps), puis faire les insertions SQLite par **lots transactionnels** depuis un composant propriétaire unique de la base.
9. Publier métriques et progression de chaque segment traité.

**Spécification pHash cohérente :** redimensionner en taille fixe (p. ex. 32×32), calculer la DCT, prendre les basses fréquences, définir précisément le masque de 64 coefficients et le traitement du coefficient DC, binariser par rapport à une médiane documentée. Le stockage et la comparaison utilisent **exactement la même convention**. `int.bit_count()` peut servir à calculer la **distance de Hamming** entre deux empreintes.

**Très important :** utiliser FFmpeg de manière sûre via `subprocess` avec des arguments séparés (pas `shell=True` avec des noms fournis par l'utilisateur), vérifier les codes de sortie, gérer stderr, timeouts et fin des sous-processus. Contrôler les threads internes de FFmpeg/OpenCV pour éviter une sursouscription CPU si plusieurs processus sont lancés.

### 5.2 Index de candidats : éviter les comparaisons quadratiques

Il serait inefficace de comparer toutes les images du clip à toutes les images des films à chaque recherche.

Construire un **index de recherche de candidats**, par exemple :

- Découper chaque pHash de 64 bits en **4 bandes de 16 bits**.
- Stocker `fingerprint_id`, `band_index`, `band_value` dans SQLite, avec un index `(band_index, band_value)`.
- Pour un hash requête, récupérer les fingerprints partageant certaines bandes, puis calculer leur vraie distance de Hamming.
- Dédupliquer les candidats et limiter leur nombre à l'aide de seuils configurables.
- Reconnaître honnêtement que les 4 bandes sont un **filtre approximatif** : des voisins peuvent manquer si plusieurs bits changent dans toutes les bandes. Mesurer le **rappel** sur les extraits transformés ; envisager plus de bandes, un multi-probe ou un fallback si la qualité est insuffisante.
- Une **recherche exhaustive de référence** reste disponible sur de petits corpus pour comparer l'exactitude et mesurer l'amélioration, mais pas comme algorithme systématique de production.

**SQLite :** le `pHash` non signé 64 bits risque de dépasser l'intervalle de l'entier signé SQLite. Le stocker de manière stable en **BLOB de 8 octets** ou en **chaîne hexadécimale de 16 caractères** ; convertir sans perdre de bits.

### 5.3 Reconnaissance d'un extrait et alignement temporel

1. Vérifier que le clip est valide et que sa durée est dans la fenêtre autorisée.
2. Extraire les pHash avec **les mêmes paramètres** que l'index ; conserver les timestamps **relatifs au clip**.
3. Récupérer des correspondances approximatives dans l'index de candidats.
4. Chaque correspondance associe `(query_timestamp, movie_timestamp)` ; calculer `offset = movie_timestamp - query_timestamp`.
5. Grouper/voter sur les couples `(movie_id, offset_bucket)` pour identifier un placement probable de l'extrait ; autoriser une petite tolérance de décalage dû à l'échantillonnage.
6. Vérifier **l'ordre temporel**, l'espacement et la cohérence des images alignées sur toute la durée du clip ; ne jamais accepter un film sur la seule base d'une image ressemblante.
7. Classer les candidats avec un score documenté intégrant distance de Hamming, couverture temporelle, nombre de correspondances cohérentes, stabilité de l'offset et marge par rapport au concurrent suivant.
8. Rejeter les matches faibles ou ambigus et retourner un résultat négatif clair.
9. Calculer la plage `start_ms`, `end_ms` et l'afficher au format `HH:MM:SS`.

**Terminologie correcte :** appeler l'indicateur « score de similarité » et non « probabilité de 96 % » tant qu'aucune calibration probabiliste n'a été démontrée. Définir et tester les seuils sur un **ensemble de validation distinct du test final**.

### 5.4 Cas indispensables

- Clip au début, au milieu et à la fin d'un film.
- Clip qui traverse une frontière de segment.
- Début non aligné avec une seconde entière.
- Clip recompressé, redimensionné, éventuellement FPS modifié.
- Fond noir, scène sombre, générique ou scène répétée.
- Deux films contenant des passages très similaires.
- Clip absent de tous les films : **aucun résultat fiable**.
- Film modifié ou supprimé après l'indexation : invalidation et cohérence de la base.

L'audio n'est **pas requis**. Ne pas ajouter de modèles de reconnaissance vocale ou de transcription automatique pour la V1.

---

## 6. Concurrence : appliquer réellement les chapitres pertinents

> **Principe fondamental :** CPU-bound → plusieurs **processus** ; I/O bloquantes → **threads** bornés ; coordination de requêtes et d'attentes → **asyncio**. Ne jamais introduire une technique si elle n'apporte rien.

### 6.1 `multiprocessing` : parallélisme CPU réel (CHAPITRE 2)

**Utilisation principale :** indexation de films découpés en segments indépendants ; pHash / décodage / normalisation, et éventuellement vérification lourde si un profilage le justifie.

- Utiliser `concurrent.futures.ProcessPoolExecutor` (adossé à `multiprocessing`) **ou** `multiprocessing.Pool`. Choisir une seule solution cohérente, la justifier, et réutiliser le pool plutôt que le recréer pour chaque frame.
- Les fonctions exécutées doivent être définies **au niveau module** et recevoir des objets simples sérialisables : chemins validés, bornes temporelles, paramètres.
- Sous Windows, le démarrage doit être compatible `spawn` et tous les scripts de lancement qui créent des processus doivent utiliser la protection `if __name__ == "__main__":` lorsqu'elle est requise. Initialiser proprement le pool dans le cycle de vie de l'application FastAPI, et **ne pas utiliser plusieurs workers Uvicorn**, ce qui dupliquerait les pools et la charge mémoire.
- Une tâche = un **segment**, pas une image. Choisir une granularité qui équilibre coût de lancement, disque, CPU et mémoire.
- Ne jamais transmettre de frames brutes volumineuses entre processus ; transmettre essentiellement empreintes et timestamps compacts, ou petits artefacts temporaires contrôlés.
- **Un seul propriétaire des écritures SQLite** : aucun processus du pool n'écrit directement dans la base commune.
- Traiter les erreurs et les `Future` échouées, nettoyer les FFmpeg enfants, arrêter proprement le pool, fournir une annulation **coopérative entre segments** (les tâches déjà engagées peuvent finir avant annulation).
- Limiter la concurrence FFmpeg et OpenCV : commencer avec **2 processus** sur 16 Go et tester 1, 2, 4 si le CPU permet. Ne pas supposer que 4 est forcément plus rapide que 2.
- Si une `multiprocessing.Queue` ou un `Pipe` est nécessaire pour une autre version du pipeline, l'utiliser correctement (messages bornés, sentinelles, `join()`), **mais ne pas forcer cette primitive** si `ProcessPoolExecutor` et ses futures suffisent.

**Justification à documenter :** processus isolés en mémoire, plusieurs cœurs CPU, différences avec threads, coût de sérialisation et de démarrage des processus, synchronisation et surcharge de communication inter-processus.

### 6.2 `threading` : E/S bloquantes locales (CHAPITRE 1)

**Utilisation recommandée et concrète :**

- `ThreadPoolExecutor(max_workers=2)` pour tâches **bloquantes de fichiers** : calcul de SHA-256 en flux, vérification de fichiers, lecture de métadonnées, génération de vignettes ; ne pas encombrer l'event loop de ces opérations.
- Si le volume des résultats le nécessite, un petit pipeline producteur–consommateur local `queue.Queue(maxsize=...)` avec **un unique thread d'écriture SQLite**, qui réalise les transactions par lots. Dans le cas contraire, une file d'écritures sérialisées avec un seul exécuteur d'E/S est également acceptable ; choisir une architecture **simple et testable**, pas les deux en même temps sans raison.
- `threading.Event` pour arrêter proprement un service auxiliaire et vider les écritures critiques.
- `threading.Lock` **uniquement** pour protéger une structure effectivement partagée entre threads ; ne pas verrouiller des objets déjà protégés par `queue.Queue`.
- `Semaphore`/`BoundedSemaphore` uniquement pour limiter une ressource bloquante réelle ; `Condition` uniquement s'il existe un vrai prédicat partagé à attendre (`while not condition: wait()` sous verrou) ; `RLock` seulement si une réentrance démontrée l'exige.

**Interdit :** lancer un thread par image, utiliser des threads Python pour remplacer les processus sur du pHash CPU pur, synchroniser avec des `sleep()` arbitraires, multiplier les verrous inutiles, utiliser des threads daemon pour perdre des écritures SQLite importantes.

### 6.3 `asyncio` : API non bloquante et coordination locale

**Utilisation :**

- Endpoints FastAPI asynchrones pour les requêtes courtes et le suivi des jobs.
- Gestionnaire de jobs qui reçoit une demande, crée un identifiant, réserve des ressources, déclenche les tâches et publie leurs états sans bloquer les autres requêtes.
- `asyncio.create_task`, `asyncio.Semaphore`, `asyncio.Queue` bornées et `asyncio.wait_for` lorsqu'ils sont utiles ; gérer exceptions, fermeture et annulations.
- Déléguer les traitements lourds à `ProcessPoolExecutor` via `loop.run_in_executor(...)`, et les I/O synchrones ciblées à `ThreadPoolExecutor` / `asyncio.to_thread` selon l'architecture choisie.
- Ne **jamais** faire un `time.sleep()`, un long `subprocess.run()` ou une boucle de fingerprinting CPU directement dans l'event loop.
- Faire consulter la progression au frontend par `GET /api/jobs/{id}` toutes les ~0,8–1,5 s ; **le polling suffit et évite toute architecture de socket supplémentaire**.

**À expliquer :** `asyncio` gère principalement les périodes d'attente et l'ordonnancement coopératif, **pas** le calcul parallèle multicœur.

### 6.4 Coordination, intégrité, annulation

- États locaux de job : `queued → running → completed / failed / cancelled` ; sous-tâches identifiées par `(movie_id, segment_start_ms, fingerprint_version)`.
- Taille **bornée** de la file de tâches et nombre limité de jobs simultanés : ne pas saturer disque et RAM avec plusieurs indexations massives.
- Préférer un propriétaire unique de l'état partagé ; sections critiques courtes lorsque de vrais threads écrivent la même structure.
- Indexations concurrentes du **même film** interdites ou dédupliquées ; suppression pendant indexation sûre ; transactions idempotentes `UNIQUE(movie_id, timestamp_ms, algorithm_version)` ou équivalent.
- Un échec sur un segment entraîne une erreur explicite, éventuellement une reprise limitée, jamais un index partiel présenté comme complet.
- Arrêt de l'application : refuser nouveaux jobs, demander l'arrêt coopératif, terminer/annuler raisonnablement les futures, vider les écritures, fermer FFmpeg, pool et DB.
- Conserver des informations de progression honnêtes et détaillées ; ne pas promettre une reprise après panne système complète si elle n'existe pas.

---

## 7. Base SQLite et index persistants

Tables recommandées (à adapter uniquement avec justification) :

| Table               | Champs clés et rôle                                                                                                       |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `movies`            | `id`, `title`, `year`, `stored_path`, `file_size`, `content_hash`, `duration_ms`, `status`, `index_version`, `created_at` |
| `segments`          | `id`, `movie_id`, `start_ms`, `end_ms`, `status`, `fingerprints_count`, `processing_ms`, `error`                          |
| `fingerprints`      | `id`, `movie_id`, `segment_id`, `timestamp_ms`, `hash_hex` ou `hash_blob`, `informativeness`, `algorithm_version`         |
| `fingerprint_bands` | `fingerprint_id`, `band_index`, `band_value`, avec index SQL `(band_index, band_value)`                                   |
| `jobs`              | `id`, `kind`, `movie_id`, `state`, `completed_units`, `total_units`, `started_at`, `finished_at`, `error`                 |
| `searches`          | `id`, `status`, `query_duration_ms`, `best_movie_id`, `start_ms`, `score`, `result_json`                                  |
| `benchmarks`        | `id`, `mode`, `process_count`, `dataset_id`, `elapsed_ms`, `frames_count`, `peak_ram_mb`, `metrics_json`                  |

- Utiliser SQLAlchemy + SQLite avec migrations ou initialisation de schéma documentée.
- Transactions atomiques, contraintes d'unicité, clés étrangères et index pertinents.
- Éviter des connexions/cursors SQLite partagés arbitrairement entre threads ou processus. Chaque opération BD utilise une session gérée dans un contexte défini ; **sérialiser les écritures** si besoin. WAL peut être utilisé lorsque pertinent.
- En cas de réindexation, ne pas mélanger deux versions incompatibles ; mettre à jour l'état de façon atomique et gérer les erreurs.
- La base, les miniatures et les empreintes sont **sur le disque du PC local**. Aucun stockage cloud.

---

## 8. API FastAPI locale

Documenter les endpoints automatiquement sur `/docs` et utiliser Pydantic pour valider entrées/sorties.

| Méthode  | Route                       | Comportement                                        |
| -------- | --------------------------- | --------------------------------------------------- |
| `GET`    | `/api/health`               | Test backend, SQLite, FFmpeg, versions              |
| `GET`    | `/api/movies`               | Liste et statut des films                           |
| `POST`   | `/api/movies`               | Upload contrôlé ; retourne film et job d'indexation |
| `GET`    | `/api/movies/{id}`          | Métadonnées du film                                 |
| `DELETE` | `/api/movies/{id}`          | Suppression cohérente                               |
| `POST`   | `/api/movies/{id}/reindex`  | Relancer indexation                                 |
| `POST`   | `/api/search`               | Upload d'un clip et lancement de recherche          |
| `GET`    | `/api/search/{id}`          | Statut, résultat ou absence de correspondance       |
| `GET`    | `/api/jobs`                 | Liste des jobs                                      |
| `GET`    | `/api/jobs/{id}`            | État et progression réels                           |
| `POST`   | `/api/jobs/{id}/cancel`     | Annulation coopérative                              |
| `GET`    | `/api/benchmarks`           | Résultats enregistrés                               |
| `POST`   | `/api/benchmarks/run`       | Benchmark local autorisé                            |
| `GET`    | `/api/media/{id}/thumbnail` | Miniature sûre d'un film géré                       |

- Les longues indexations doivent répondre rapidement avec `202 Accepted` et un identifiant de job, pas garder la requête HTTP ouverte pendant plusieurs minutes.
- Gérer validations réelles, taille maximale, type de contenu, erreurs de décodage, fichiers corrompus et manque d'espace disque.
- Stocker et servir uniquement des fichiers dans les répertoires autorisés. Éviter traversées `../`, noms dangereux et commandes shell interpolées.
- Les imports vidéo volumineux ne doivent pas être chargés d'un seul bloc en RAM ; écriture en flux/buffers bornés vers un fichier temporaire puis validation et renommage atomique.
- Autoriser explicitement l'origine du frontend local (`http://localhost:3000`) en développement, sans ouverture réseau publique.
- **Un seul processus serveur Uvicorn** par défaut ; le parallélisme CPU provient du pool interne, pas du nombre de réplicas du serveur web.

---

## 9. Interface Next.js professionnelle, sobre et utile

Développer une vraie UI en français (Next.js App Router + TypeScript + Tailwind, composants accessibles, icônes Lucide, graphiques si nécessaires).

**Pages :**

1. **Accueil / Tableau de bord** : total de films, heures indexées, empreintes, traitements en cours, dernières recherches, raccourci « Identifier un film ».
2. **Bibliothèque** : importer une ou plusieurs vidéos, vignettes, tableau/galerie, progression, statut, suppression/réindexation.
3. **Reconnaissance** : glisser-déposer un clip, prévisualisation, chargement et résultat clair : bon film, timeline, horodatage, score et couverture. Afficher proprement « Aucun résultat » ou « Résultat ambigu ».
4. **Traitements** : jobs locaux, barres de progression réelles, nombre de processus utilisés, erreurs et annulation.
5. **Performances** : choix séquentiel / 2 processus / 4 processus (selon CPU), benchmark, graphiques temps, speedup, frames/s, mémoire ; export.

**Design :** interface moderne mais non surchargée, thème sombre soigné, responsive, empty states, erreurs utiles, animations discrètes, vrais fichiers miniatures et vrais graphiques. Aucun écran « Cluster », aucune IP de worker, aucun contrôle de PC distant.

**Consignes front :**

- Utiliser un client API typé et un polling HTTP pour les jobs (intervalle raisonnable, arrêt lorsque le job se termine).
- Éviter les rendus qui rechargent une énorme liste d'empreintes ; seules les informations synthétiques et métadonnées transitent vers le navigateur.
- Ne pas inventer des données de démo dans les cartes en mode production.
- Formats lisibles pour les durées et les octets ; distinguer similarité heuristique et probabilité.

---

## 10. Paramètres prudents pour 16 Go de RAM et Windows

Créer un fichier `.env.example` similaire à :

```env
APP_HOST=127.0.0.1
APP_PORT=8000
FRONTEND_PORT=3000
DATABASE_URL=sqlite:///./data/movieshazam.db
MEDIA_ROOT=./data/media
TEMP_ROOT=./data/temp
FINGERPRINT_VERSION=phash-v1
FINGERPRINT_FPS=2
SEGMENT_DURATION_SECONDS=180
PROCESS_WORKERS=2
IO_THREADS=2
MAX_ACTIVE_INDEX_JOBS=1
MAX_ACTIVE_SEARCH_JOBS=2
MAX_FFMPEG_CONCURRENT=2
MAX_QUERY_DURATION_SECONDS=30
MAX_UPLOAD_GB=8
```

- Les nombres sont **des paramètres initiaux**, non des performances garanties.
- Adapter le nombre de processus à `os.cpu_count()`, à la RAM, à la disponibilité CPU et à l'I/O disque ; démarrer avec **2** pour éviter les saturations.
- FFmpeg peut exploiter ses propres threads ; profiler et limiter le parallélisme interne si nécessaire.
- Dépenses mémoire plafonnées : queue bornée, extraction de frames en flux, chunks de petite taille, inserts par lots, nettoyage des fichiers temporaires.
- Un SSD est recommandé pour la réactivité, mais l'application doit fonctionner avec un disque classique. Les **films eux-mêmes** peuvent nécessiter beaucoup d'espace disque, malgré la faible taille des empreintes.
- Ne pas nécessiter Internet à l'exécution, hormis l'installation initiale éventuelle des paquets. Ne pas écouter sur `0.0.0.0` par défaut.

---

## 11. Corpus de démonstration : générer toutes les données soi-même

Créer `scripts/generate_demo_videos.py` :

- Générer **3 à 5 vidéos synthétiques distinctes** (30–120 s), avec textures/motifs, mouvement et changements de plans suffisamment discriminants. Éviter les écrans uniformes ou des scènes entièrement identiques.
- Extraire plusieurs clips de 5, 10 et 20 secondes, à différents timestamps (dont un commencement non multiple de la seconde et un clip traversant une frontière de segment lorsque le corpus est assez long).
- Créer des variantes par FFmpeg : recompression H.264, résolution réduite, éventuellement fréquence d'image différente.
- Créer un clip **inconnu**, absent de toute la bibliothèque indexée.
- Écrire `ground_truth.json` avec film d'origine, début/fin réels et transformation ; graine et génération déterministes.
- Ces vidéos sont des **données de test**, pas des résultats codés en dur : les mêmes algorithmes doivent fonctionner avec des clips filmés par l'équipe ou librement utilisables.

**Attention :** une bibliothèque de 50 films sert à tester la montée en charge **si l'étudiant dispose légalement des médias**. Ne pas nécessiter ces 50 films pour la démonstration minimale.

---

## 12. Tests automatiques obligatoires

**Unitaires (`pytest`) :**

1. pHash déterministe et distance de Hamming correcte.
2. Sérialisation/stockage stable des 64 bits, y compris valeurs extrêmes.
3. Index par bandes : retrouver un candidat exact ; mesurer les faux négatifs du filtre approximatif sur variations contrôlées.
4. Cohérence des timestamps globaux, segmentation et éventuel chevauchement.
5. Alignement temporel : retrouver un offset correct ; **ne pas** identifier une vidéo seulement parce que quelques images ressemblent.
6. Rejeter un clip inconnu ou une correspondance trop ambiguë.
7. Empêcher doublons d'empreintes et mélange de versions d'algorithme.
8. Annulation de job, erreur de segment, nettoyage des ressources, limites de concurrence.
9. Simuler une situation de compétition sur un état réellement partagé, et vérifier que la solution `Queue` / `Lock` choisie préserve la cohérence.
10. Vérifier que le mode séquentiel et parallèle retournent les **mêmes empreintes et résultats** pour la même entrée et le même algorithme.

**Intégration :**

- Indexer réellement au moins deux vidéos ; reconnaître le bon film et un horodatage proche de la vérité terrain (tolérance explicitée, par exemple ±1 s sur le cas simple à 2 FPS).
- Réussir des variantes recompresées/redimensionnées **dans un jeu de validation fixé** ; documenter les échecs.
- Clip inconnu → absence de résultat fiable.
- Indexation répétée → pas de recalcul/doublons injustifiés.
- Échec FFmpeg → état `failed`, message explicite et API toujours réactive.
- Redémarrage de l'application → films et empreintes présents, réutilisables.
- Deux processus locaux réellement distincts contribuent à un benchmark parallèle ; vérifier la différence de PID ou des logs de tâches sans inventer les valeurs.
- Next.js → FastAPI → moteur réel → SQLite → résultat UI : test de bout en bout.

Ne pas se contenter de tests de fonctions mockées. Les tests de décodage et de reconnaissance doivent utiliser de **vrais fichiers vidéo générés**.

---

## 13. Benchmarks académiques sérieux et reproductibles

Créer `scripts/run_benchmark.py`, la page « Performances » et un export de résultats :

1. Identifier CPU, nombre de cœurs, RAM, version Python, FFmpeg et paramètres du moteur.
2. Utiliser un **corpus identique** et les **mêmes paramètres** pour chaque mode.
3. Tester `1`, `2`, éventuellement `4` processus, avec chaque fois des tâches assez nombreuses pour rendre le parallélisme pertinent.
4. Chronométrer à l'aide de `time.perf_counter()` ; séparer si possible décodage, calcul des empreintes et écriture SQLite.
5. Mesurer durée, nombre d'images, throughput, mémoire, cœurs occupés et taux d'erreurs.
6. Calculer `speedup(N) = T_sequential / T_parallel(N)` et `efficiency(N) = speedup(N) / N` ; ne pas supposer qu'ils sont > 1.
7. Tester **à froid et à chaud** ou documenter l'effet du cache disque ; éviter de comparer une première exécution sans cache à une seconde entièrement mise en cache.
8. Éviter qu'une indexation persistante déjà présente fausse le temps : utiliser un mode benchmark isolé avec réinitialisation contrôlée et traitement équivalent.
9. Comparer la recherche exhaustive vs l'index de candidats sur le même corpus, avec **précision/rappel** et temps de recherche. Accélérer la recherche en sacrifiant trop de précision n'est pas une bonne amélioration.
10. Expliquer dans la présentation les cas où lancer plus de processus **ralentit** l'application (overhead, SSD/HDD, contention, FFmpeg multithreadé).

**But :** prouver l'apport du calcul parallèle **local**, pas mettre en scène une performance inexistante ou inventer un réseau distribué.

---

## 14. Qualité, sécurité et robustesse

- Architecture modulaire, typage, fonctions testables, docstrings utiles, séparation API / moteur / jobs / persistance / UI.
- Validation des uploads, quotas disque/tailles, limites des clips, erreurs détaillées mais sûres, pas de chemins arbitraires transmis à FFmpeg.
- Pas de `shell=True` pour les fichiers utilisateur ; répertoires temporaires sûrs, nettoyage après interruption, vérification du chemin canonique.
- Ne pas exposer les fichiers arbitraires du PC ni activer une exécution de commandes utilisateur.
- Gestion des requêtes simultanées : pas de corruption SQLite, pas de démarrage incontrôlé de processus, pas de suppression pendant un commit critique.
- Logs structurés avec `job_id`, `movie_id`, `segment_id`, PID de processus utile, timestamps et erreurs exploitables. Pas de données inventées.
- Ajouter une commande de contrôle qualité (formatage/lint et tests) et documenter les échecs connus.
- Préférer **une solution simple et maintenable** à des couches de services ou protocoles superflus.

---

## 15. Structure souhaitée du dépôt

```text
movieshazam/
├── backend/
│   ├── app/
│   │   ├── main.py                 # FastAPI + lifespan
│   │   ├── api/                    # routes et schemas Pydantic
│   │   ├── core/                   # settings, logging, utils
│   │   ├── video/
│   │   │   ├── ffmpeg_io.py         # ffprobe, extraction streaming
│   │   │   ├── fingerprint.py       # pHash + Hamming
│   │   │   ├── segmentation.py      # segments/timestamps
│   │   │   └── matching.py          # candidats + alignement
│   │   ├── concurrency/
│   │   │   ├── process_pool.py      # ProcessPoolExecutor local
│   │   │   ├── thread_io.py         # I/O bornée
│   │   │   └── job_manager.py       # orchestration asyncio locale
│   │   ├── db/                    # models, sessions, repositories
│   │   └── services/              # movie, index, search, benchmark
│   ├── tests/
│   ├── pyproject.toml
│   └── .env.example
├── frontend/
│   ├── app/                       # Next.js App Router
│   ├── components/
│   ├── lib/                        # client API typé
│   └── package.json
├── scripts/
│   ├── generate_demo_videos.py
│   ├── run_benchmark.py
│   ├── start_windows.ps1
│   └── start_unix.sh
├── data/                         # local, ignoré par Git
├── docs/
│   ├── CONCURRENCY_EXPLAINED.md
│   ├── DEMO_SOUTENANCE.md
│   └── PLAN_PRESENTATION.md
├── .gitignore
└── README.md
```

Cette arborescence est indicative : adapter si cela **simplifie** réellement. **Ne pas** créer de dossier `cluster`, `tcp_protocol`, `remote_worker`, `network_scheduler` ni de script pour rejoindre un autre ordinateur.

---

## 16. Ordre d'implémentation impératif

### Phase 1 — Algorithme vérifiable (avant le frontend)

1. Créer le générateur de vidéos/extraits et leur vérité terrain.
2. Implémenter l'extraction FFmpeg, le pHash, les timestamps et un moteur de recherche **séquentiel** simple.
3. Démontrer par tests qu'un clip propre retrouve le bon film et le bon passage, et qu'un clip absent est refusé.

### Phase 2 — Base et indexation intelligente

1. SQLite et tables de fingerprints, index par bandes, insertions bornées par lots.
2. Recherche avec candidats et alignement temporel ; calibrer seuils sur validation.
3. Index incrémental, version d'algorithme, gestion des erreurs.

### Phase 3 — Parallélisme CPU local

1. Découper les films en tâches de segments ; créer un **seul pool de processus local**.
2. Implémenter le mode séquentiel et le mode parallèle avec la même fonction métier.
3. Vérifier les résultats identiques ; mesurer 1/2/4 processus, RAM, FFmpeg et CPU.
4. Assurer arrêt et erreurs sans fuite de processus.

### Phase 4 — FastAPI, asyncio, threading

1. Exposer bibliothèque, recherche et jobs via REST.
2. Ajouter gestion asynchrone des travaux et polling de progression.
3. Ajouter threads **uniquement pour E/S bloquantes utiles**, protéger les ressources partagées réellement nécessaires.
4. Vérifier que l'API répond pendant un travail intensif et que SQLite reste cohérent.

### Phase 5 — Interface Next.js

1. Construire l'accueil, la bibliothèque, la reconnaissance, les traitements et les performances.
2. Connecter les pages aux **vraies API**, sans mocks dans l'application finale.
3. Gérer uploads, erreurs, attente, résultats, timeline et absence de match.

### Phase 6 — Validation et livrables

1. Tests unitaires + intégration + end-to-end.
2. Benchmarks reproductibles exportables.
3. Scripts de lancement Windows et Unix ; README clair, documentation de concurrence et de soutenance.
4. Tester démarrage depuis une installation propre sur Windows et ressources limitées.

**Après chaque phase : implémenter, exécuter, corriger, puis passer à la suivante.** Ne pas passer une semaine à faire une maquette avant d'avoir un moteur opérationnel.

---

## 17. Mise en route locale simple

Le `README.md` doit expliquer précisément comment :

1. Installer Python, Node.js et FFmpeg et vérifier leurs versions.
2. Installer les dépendances backend dans un environnement virtuel.
3. Copier `.env.example`, créer les dossiers locaux et initialiser SQLite.
4. Générer le corpus vidéo de démonstration.
5. Démarrer **un seul backend FastAPI** et **un frontend Next.js** sur le même PC.
6. Importer les vidéos, lancer l'indexation, rechercher un extrait et consulter le résultat.
7. Exécuter les tests et le benchmark local.

Fournir `start_windows.ps1` pour Windows et `start_unix.sh` pour Linux/macOS ; pas de commande pour démarrer des workers réseau, pas d'ouverture de port vers le LAN. Les scripts doivent afficher les commandes, gérer les erreurs et ne jamais supprimer arbitrairement des vidéos personnelles.

---

## 18. Documentation pour expliquer le projet à la professeure

### `docs/CONCURRENCY_EXPLAINED.md`

Expliquer avec des **extraits réels du code**, des schémas simples et des mesures :

| Technique                | Problème concret résolu dans MovieShazam                   | Preuve                                    |
| ------------------------ | ---------------------------------------------------------- | ----------------------------------------- |
| Séquentiel               | Baseline déterministe                                      | Résultats + temps de référence            |
| `multiprocessing`        | Indexation de segments CPU-bound sur plusieurs cœurs       | PIDs, mêmes hashes, temps 1/2/4 processus |
| `threading`              | E/S disque bloquantes, gestion sûre de certaines écritures | Réactivité + tests de cohérence           |
| `asyncio`                | Coordonner jobs et requêtes API sans bloquer               | API disponible pendant indexation         |
| `Queue`, `Lock`, `Event` | File/état partagé **si** réellement nécessaire             | Tests de concurrence, arrêt propre        |

Distinguer CPU-bound / I/O-bound, espace mémoire des processus vs threads, GIL, `Pool`/`ProcessPoolExecutor`, IPC, sections critiques, contention, coûts de synchronisation et limites du speedup. Ne pas prétendre appliquer un outil qui n'existe pas dans le code.

### `docs/DEMO_SOUTENANCE.md`

Préparer une démonstration **sur un seul PC**, reproductible :

1. Présenter 3–5 vidéos et leur indexation réelle.
2. Importer un clip connu de 10 s ; obtenir bon film et timestamp.
3. Refaire après recompression et redimensionnement.
4. Essayer un clip inconnu ; afficher le refus correct.
5. Montrer indexation séquentielle et parallèle, mesures de temps et PIDs locaux.
6. Afficher le suivi des jobs pendant que l'interface reste réactive.
7. Montrer un test d'échec/annulation, sans base corrompue.
8. Montrer des tests automatisés réellement exécutés.

### `docs/PLAN_PRESENTATION.md`

Plan de **10 à 12 slides** : problème, utilité, exemples d'extraits, méthodes pHash, alignement temporel, architecture locale, multiprocessing, threading, asyncio, SQLite/index de recherche, benchmarks, résultats de tests, limites et conclusion. Notes orales courtes ; **aucun chiffre de performance inventé**.

---

## 19. Critères d'acceptation : ne déclarer « terminé » que si…

- [ ] Application lancée **entièrement sur un seul PC**, sans GPU, cloud, worker distant ni protocole TCP personnalisé.
- [ ] Frontend Next.js français fonctionnel et connecté au vrai backend Python.
- [ ] Indexation de vraies vidéos avec FFmpeg, empreintes visuelles et timestamps persistants.
- [ ] Clip connu reconnu avec le bon film et un horodatage validé.
- [ ] Clip inconnu et correspondance ambiguë traités sans résultat inventé.
- [ ] Recompression et redimensionnement évalués sur corpus de test fixe.
- [ ] Indexation incrémentale et suppression cohérente.
- [ ] Moteur séquentiel de référence et vrai `ProcessPoolExecutor` local.
- [ ] Threads limités à des usages I/O réels ; synchronisation correcte et minimale.
- [ ] `asyncio` coordonne l'application sans bloquer l'event loop par les calculs.
- [ ] États/progression des jobs issus des traitements réels.
- [ ] Gestion FFmpeg, processus, mémoire, exceptions et annulation propre.
- [ ] Mesures 1/2/4 processus réelles avec dataset reproductible.
- [ ] Tests unitaires, intégration et démonstration de bout en bout exécutés.
- [ ] README, scripts de lancement et documentation pédagogique livrés.

## **Aucun écran fictif. Aucune fonctionnalité seulement décrite. Aucune architecture distribuée ni dépendance réseau entre ordinateurs.**

## 20. Instructions finales à l'agent de codage

1. Inspecte le dépôt existant ; conserve le code valide et répare le code incomplet si nécessaire.
2. Écris un **plan de réalisation très court**, puis commence à **coder immédiatement** le générateur vidéo et la reconnaissance réelle.
3. Implémente les phases **dans l'ordre** ; exécute tests et smoke tests à chaque étape.
4. Si FFmpeg manque dans ton environnement, prépare les commandes d'installation et les messages d'erreur ; n'invente pas une sortie de test.
5. Optimise d'abord **la justesse du matching et l'utilisation mémoire**, ensuite les performances CPU, et enfin l'esthétique.
6. Ne rajoute **aucun** serveur TCP de workers, aucune machine distante, aucune démonstration de cluster, ni aucun service externe « au cas où ».
7. Si le temps manque, livre une V1 solide (indexation + recherche + multiprocessing + UI + tests) plutôt que beaucoup de modules vides.
8. Termine par un rapport indiquant : fonctionnalités **réellement implémentées**, fichiers importants, commandes pour démarrer, tests **effectivement exécutés**, benchmarks mesurés, problèmes encore connus et déroulé de la démonstration.

**OBJECTIF FINAL : un MovieShazam utile, efficace et vérifiable, entièrement local, avec un moteur de reconnaissance de scènes fonctionnel et une application rigoureuse des techniques Python de programmation parallèle et concurrente étudiées dans le cours.**
