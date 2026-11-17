# La concurrence dans MovieShazam, expliquée avec le vrai code

Module *Systèmes distribués et parallèles* — ISAMM 2026-2027. Ce document relie chaque technique
du cours au **problème concret** qu'elle résout dans MovieShazam, avec des extraits du code et la
**preuve** (test ou mesure) qu'elle fonctionne. Tout s'exécute sur **un seul PC** : le chapitre 4
(sockets) n'est volontairement pas utilisé, il n'y a ni worker distant ni protocole réseau.

**Choix d'architecture : processus + threads, sans asyncio.** Le calcul CPU est confié à des
**processus** (`multiprocessing` via `ProcessPoolExecutor`), tout le reste — requêtes HTTP, E/S
bloquantes, travaux d'arrière-plan, écritures SQLite — à des **threads** (`threading`,
`ThreadPoolExecutor`, `queue.Queue`). Le code de l'application ne contient aucune coroutine
(`async def`) ni aucun appel au module `asyncio` ; un test le vérifie
(`test_application_code_does_not_use_asyncio`).

| Technique | Problème concret résolu | Où dans le code | Preuve |
|---|---|---|---|
| Séquentiel | Référence déterministe | `services/indexing.py` (`pool=None`) | `test_sequential_and_parallel_produce_identical_index`, colonne « séquentiel » des benchmarks |
| `multiprocessing` (`ProcessPoolExecutor`) | Indexer des segments de film sur plusieurs cœurs | `concurrency/process_pool.py`, `video/pipeline.py` | PID distincts, empreintes identiques, temps 1/2/4 processus |
| `threading` — requêtes | Servir plusieurs requêtes HTTP en même temps | routes `def` de `api/routes.py` (pool de threads de FastAPI) | `GET /api/jobs/{id}` en ~4 ms pendant une indexation |
| `threading` — E/S | Copie, SHA-256, ffprobe, vignettes ; écrivain SQLite unique | `concurrency/thread_io.py`, `db/writer.py` | tests de course, arrêt sans perte d'écriture |
| `threading` — travaux de fond | Indexation, recherche, benchmark sans bloquer les requêtes | `concurrency/job_manager.py` | tests d'annulation/reprise, test de polling concurrent |
| `queue.Queue`, `Lock`, `Event`, `Condition`, `Barrier` | File bornée, état partagé, arrêt propre, exclusivité | voir § 5 | `test_concurrency.py`, `test_api.py` |

---

## 1. CPU-bound ou I/O-bound ? Le critère qui décide de tout

* **CPU-bound** : le temps est passé à *calculer* (décoder une vidéo, réduire une image, calculer une DCT).
  Plus de cœurs = plus de calcul simultané… si le code peut s'exécuter en parallèle.
* **I/O-bound** : le temps est passé à *attendre* (disque, sous-processus, réseau local, base de données).
  On peut attendre plusieurs choses à la fois sans plusieurs cœurs.

En CPython, le **GIL** (Global Interpreter Lock) n'autorise qu'**un thread à la fois** à exécuter du
bytecode Python. Conséquences :

| | Threads (`threading`) | Processus (`multiprocessing`) |
|---|---|---|
| Mémoire | partagée | **séparée** (chaque processus a la sienne) |
| Parallélisme CPU réel | non pour du Python pur (GIL) | **oui** |
| Coût de création | faible | élevé (Windows : *spawn* = nouvel interpréteur) |
| Communication | variables partagées + verrous | sérialisation *pickle* via files/pipes (IPC) |
| Usage MovieShazam | requêtes HTTP, E/S bloquantes, jobs de fond, écrivain SQLite | indexation des segments |

Le GIL est relâché pendant les appels système bloquants (lecture de fichier, attente d'un sous-processus,
attente d'un `Future`) : c'est pourquoi des threads conviennent très bien aux E/S et à la coordination,
mais pas au calcul Python pur.

## 2. `multiprocessing` : l'indexation parallèle (chapitre 2)

### 2.1 Découper en unités de travail indépendantes

Un film est découpé en **segments** (`video/segmentation.py`), par exemple 180 s pour un long-métrage,
15 s pour un film de démonstration de 2 min. **Une tâche = un segment**, jamais une image : le coût
de lancement d'une tâche (sérialisation, file inter-processus) est amorti sur des centaines d'images.

Le découpage ne dépend **pas** du nombre de processus : séquentiel et parallèle traitent exactement les
mêmes segments. Toutes les bornes sont des multiples de 500 ms, donc les horodatages calculés par un
segment tombent sur la même grille globale que si le film était décodé d'un seul tenant
(vérifié : `test_segmented_decode_equals_whole_decode`).

### 2.2 La même fonction métier dans les deux modes

```python
# backend/app/video/pipeline.py — fonction de MODULE, arguments sérialisables
def analyze_segment(task: SegmentTask) -> SegmentResult:
    """Décode le segment en flux et calcule un pHash par image échantillonnée."""
    for k, frame in iter_gray_frames(task.video_path, start_ms=decode_start, ...):
        ts = decode_start + k * interval          # horodatage GLOBAL dans le film
        if ts < task.start_ms: continue          # pré-lecture : appartient au segment précédent
        if ts >= task.end_ms: break              # règle de propriété [start, end)
        value, info = phash_gray(frame)
        result.timestamps.append(ts); result.hashes.append(value) ...
    return result                                 # quelques Ko : jamais d'images brutes
```

```python
# backend/app/services/indexing.py
if pool is None:                                   # séquentiel : une simple boucle
    for seg_id, task in tasks.items():
        _save(seg_id, analyze_segment(task))
else:                                              # parallèle : mêmes tâches, fenêtre bornée
    pending[pool.submit(analyze_segment, task)] = seg_id
    done, _ = wait(pending, return_when=FIRST_COMPLETED)
```

### 2.3 Le pool : créé une fois, compatible Windows

```python
# backend/app/concurrency/process_pool.py
self._ctx = mp.get_context("spawn")               # spawn partout (défaut Windows, sûr sous Linux)
ProcessPoolExecutor(max_workers=self.workers, mp_context=self._ctx,
                    initializer=worker_initializer, initargs=(self._abort, barrier))
```

* **`spawn`** : chaque processus démarre un *nouvel interpréteur* qui ré-importe le module principal.
  C'est pourquoi les fonctions envoyées au pool sont définies au niveau du module, et pourquoi les
  scripts qui créent des processus utilisent `if __name__ == "__main__":`.
* Le pool est créé **une seule fois** au démarrage de l'application et réutilisé. On lance **un seul**
  processus Uvicorn : plusieurs workers web dupliqueraient le pool, la RAM et l'écrivain SQLite.
* `worker_initializer` fixe `cv2.setNumThreads(1)` : sans cela, chaque processus pourrait lancer
  12 threads OpenCV/NumPy (**sursouscription**). De même, chaque FFmpeg est lancé avec `-threads 1`.
* **Préchauffage avec une `multiprocessing.Barrier`** : N tâches attendent qu'il y ait N tâches en
  cours simultanément — preuve que N processus distincts existent (on relève leurs PID), sans `sleep()`.

### 2.4 Pourquoi pas des threads pour l'indexation ?

Chaque segment, c'est un décodage vidéo (dans le sous-processus FFmpeg piloté par le worker) puis
du calcul Python/NumPy (redimensionnement, DCT, médiane, empaquetage des bits). Avec des threads, la
partie Python serait sérialisée par le GIL ; avec des processus, chaque segment a son interpréteur,
sa mémoire, son FFmpeg, et un plantage dans un segment ne touche pas le serveur web.
Le coût : démarrage des processus (mesuré à part : ≈ 1–2 s pour 4 processus sous Windows),
sérialisation des résultats (minime : on ne renvoie que des entiers), et RAM (≈ 70–100 Mo par processus).

### 2.5 Annulation coopérative, erreurs, arrêt

```python
# backend/app/concurrency/job_manager.py — exécuté par un THREAD d'indexation
while backlog or pending:
    while backlog and len(pending) < window and error is None and not self._cancelled(job):
        todo_item, task = backlog.pop(0)
        pending[ctx.pool.submit(analyze_segment, task)] = todo_item   # Future du pool de processus
    done, _ = wait(pending, return_when=FIRST_COMPLETED)              # concurrent.futures.wait
    ...
    if error or self._cancelled(job):
        for fut in pending:
            fut.cancel()      # n'annule que les segments PAS ENCORE démarrés
```

* Fenêtre **bornée** de segments en vol (`2 × processus`) : jamais 200 tâches empilées en mémoire.
* Un segment en échec → erreur explicite, les autres sont annulés, le film passe en `failed` ;
  `finalize_index` refuse de marquer `indexed` un index incomplet.
* Les segments terminés restent en base : une réindexation **reprend** sans recalcul
  (`test_cancel_then_resume_indexing`).
* Arrêt de l'application : un `multiprocessing.Event` transmis à chaque processus au démarrage est
  levé ; les segments en cours s'interrompent à l'image suivante et leur FFmpeg est tué
  (`test_backend_graceful_shutdown` vérifie qu'aucun PID du pool ne survit).

## 3. `threading` : requêtes, E/S bloquantes et travaux de fond (chapitre 1)

### 3.1 Les requêtes HTTP sont servies par des threads

Toutes les routes sont des fonctions **synchrones** (`def`). FastAPI exécute chacune d'elles dans un
**thread de son pool de travail** : une requête qui attend (copie d'un upload de plusieurs Go, écriture
SQLite) occupe son thread, pendant que les autres requêtes — le *polling* de la progression par
exemple — sont servies par d'autres threads.

```python
# backend/app/api/routes.py
@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(request: Request, job_id: str) -> JobOut:
    snap = ctx.jobs.snapshot(job_id)        # copie prise sous le verrou du gestionnaire de jobs
    ...
```

Les longues opérations répondent tout de suite `202 Accepted` + identifiant de job ; le frontend
consulte `GET /api/jobs/{id}` toutes les ~1 s (**polling HTTP**) : aucune socket ni WebSocket.

### 3.2 Pool de threads borné pour les fichiers

```python
# backend/app/concurrency/thread_io.py
self._executor = ThreadPoolExecutor(max_workers=threads, thread_name_prefix="io")   # IO_THREADS=2
def call(self, fn, *args, **kwargs):
    return self._executor.submit(fn, *args, **kwargs).result()
```

Utilisé pour : copie de l'upload **par blocs de 1 Mo avec SHA-256 calculé au passage**, `ffprobe`,
génération des vignettes. Même si 40 requêtes arrivent en même temps, au plus 2 de ces opérations
touchent le disque simultanément ; les autres attendent dans la file de l'exécuteur.

### 3.3 Producteur–consommateur : un seul thread écrit dans SQLite

```python
# backend/app/db/writer.py
self._queue = queue.Queue(maxsize=maxsize)          # file bornée, déjà thread-safe : PAS de Lock
self._thread = threading.Thread(target=self._run, name="sqlite-writer", daemon=False)

def _run(self):
    while True:
        item = self._queue.get()
        if item is _STOP: return                    # sentinelle : arrêt après vidage de la file
        fn, fut = item
        with self._session_factory() as session, session.begin():   # une transaction
            fut.set_result(fn(session))
```

* SQLite n'accepte qu'un écrivain à la fois : plutôt que de laisser des threads se battre pour le
  verrou (`database is locked`), **toutes** les écritures passent par ce thread. Les processus du
  pool n'ouvrent jamais la base. Les producteurs (threads des requêtes, threads des jobs) attendent
  le résultat avec `writer.run_sync(fn)` = `submit(fn).result()`.
* Le mode **WAL** permet aux lectures (API, recherche) de se faire en parallèle de l'écriture.
* Thread **non daemon** + sentinelle FIFO + `join()` : à l'arrêt, toutes les écritures soumises sont
  vidées (`test_writer_close_drains_queue_and_refuses_new_work`).
* Bonus : « ce fichier existe-t-il déjà ? sinon l'insérer » s'exécute dans ce thread → deux imports
  simultanés du même film ne peuvent pas créer de doublon, sans verrou supplémentaire.

### 3.4 Les travaux de fond : `JobManager`

```python
# backend/app/concurrency/job_manager.py
self._index_queue = queue.Queue(maxsize=s.max_queued_jobs)            # file BORNÉE des indexations
self._index_threads = [threading.Thread(target=self._index_worker, daemon=False)
                       for i in range(s.max_active_index_jobs)]        # 1 par défaut
self._search_pool = ThreadPoolExecutor(max_workers=s.max_active_search_jobs)   # 2 recherches max

def _index_worker(self):
    while True:
        job_id = self._index_queue.get()
        if job_id is None:                       # sentinelle d'arrêt
            return
        with self._cpu_slot(exclusive=False):    # Condition : pas pendant un benchmark
            self._run_index(job)
```

* **Indexation** : une file `queue.Queue` bornée (refus 503 si elle est pleine) consommée par
  `MAX_ACTIVE_INDEX_JOBS` threads ; un film déjà en file ou en cours n'est jamais indexé deux fois.
* **Recherche** : un `ThreadPoolExecutor` à 2 threads ; le nombre de threads *est* la limite de
  recherches simultanées (pas de sémaphore supplémentaire).
* **Benchmark** : un thread dédié. `threading.Condition` réalise l'exclusivité CPU : le benchmark attend
  le prédicat « aucune indexation en cours », une indexation attend « aucun benchmark en cours »
  (`with cond: cond.wait_for(predicate)`).
* **Fin d'un job** : un `threading.Event` par job ; la suppression d'un film en cours d'indexation
  annule le job puis attend `job.done.wait(timeout)`.
* **Arrêt** : refus des nouveaux jobs, annulation des jobs en file, une sentinelle `None` par thread
  d'indexation, `join()` borné ; puis arrêt du pool de processus (segments interrompus) et dernier
  `join()`.

### 3.5 Un état réellement partagé : la table des jobs et son verrou

La progression d'un job est **écrite** par un thread de traitement et **lue** par les threads des
requêtes (polling). Sans protection, un lecteur pourrait voir un état à moitié mis à jour, ou une
erreur « set changed size during iteration » en lisant la liste des PID. Toute lecture et toute
écriture passent donc par un `threading.Lock`, en **section critique courte** (jamais pendant un calcul
ou une attente d'écriture) :

```python
with self._lock:                                   # mise à jour de la progression
    job.completed_units += 1
    job.pids.add(res.pid)

def snapshot(self, job_id):                         # lecture cohérente pour l'API
    with self._lock:
        return self._jobs[job_id].to_dict()
```

**Une vraie condition de course, évitée :** deux threads persistent le même job (le thread de
traitement écrit « terminé », un autre écrit un instantané plus ancien). Si chacun prenait son
instantané puis le mettait en file d'écriture séparément, l'ancien pourrait passer **après** le
récent et écraser l'état final en base. L'instantané et sa mise en file vers l'écrivain sont donc
faits **sous le même verrou** ; la file étant FIFO, l'ordre des écritures est celui des états. Vérifié
par `test_restart_keeps_movies_and_fingerprints` (après redémarrage, le job lu en base est
`completed`) et `test_job_state_is_consistent_under_concurrent_polling` (4 threads lisent pendant une
indexation : progression jamais décroissante, jamais supérieure au total, aucune erreur).

### 3.6 La condition de course, démontrée puis évitée

`test_race_condition_without_lock_loses_updates` : 8 threads font `v = x; x = v + 1` 2000 fois
→ le total final est **inférieur** à 16 000 (incréments perdus). Avec un `threading.Lock`
(`test_lock_preserves_consistency`) ou avec l'écrivain unique
(`test_single_writer_serializes_concurrent_sqlite_updates`, lecture-modification-écriture SQLite
depuis 8 threads) : **aucune perte**.

### 3.7 Pourquoi la recherche d'un clip utilise un thread et pas le pool de processus ?

Le clip dure au plus 30 s : son décodage est fait par un **sous-processus FFmpeg** (parallèle par
nature), et Python ne calcule que ~150–300 pHash (quelques dizaines de ms). Le thread de recherche
attend surtout le pipe (GIL relâché). L'envoyer dans le pool de processus le ferait attendre derrière
les segments d'indexation déjà en file : on garde le pool pour les traitements longs, la recherche
reste réactive.

## 4. Pourquoi pas asyncio ?

asyncio sert à multiplexer de très nombreuses attentes (des milliers de connexions réseau) dans un
seul thread. MovieShazam a au plus quelques requêtes simultanées et quelques travaux de fond : des
threads suffisent, sont plus simples à expliquer et à tester, et ne demandent pas de séparer le code
en fonctions « bloquantes » et « non bloquantes ». Le cours (chapitres 1 et 2) couvre exactement les
outils utilisés ; le support détaillé du chapitre sur asyncio n'a pas été fourni.

Précision honnête : **Uvicorn**, le serveur HTTP, utilise en interne sa propre boucle d'événements pour
lire les sockets, et FastAPI confie chaque route `def` à un thread de son pool. L'application ne
manipule jamais cette boucle : elle ne contient ni coroutine ni appel au module `asyncio`. Le démarrage
et l'arrêt sont des fonctions ordinaires (gestionnaires `on_startup` / `on_shutdown`), et la limite de
taille des envois est assurée par le middleware fourni par Starlette (`RequestBodyLimitMiddleware`).

## 5. Primitives de synchronisation : chacune pour un vrai besoin

| Primitive | Où | Pourquoi |
|---|---|---|
| `queue.Queue(maxsize)` | `DbWriter` | file producteur–consommateur bornée vers l'écrivain unique |
| `queue.Queue(maxsize)` + sentinelle `None` | `JobManager` | file bornée des indexations ; arrêt propre de ses threads |
| `ThreadPoolExecutor` | `IOExecutor`, recherches | E/S bornées à 2 ; au plus 2 recherches simultanées |
| `threading.Lock` | `JobManager._lock`, `KeyedLocks`, `ResourceSampler` | protéger un état réellement partagé entre threads (table des jobs, verrous par vignette, mesures) |
| `threading.Condition` | `JobManager._cpu` | exclusivité CPU entre benchmark et indexations (prédicat + `wait_for`) |
| `threading.Event` | fin de chaque job, `DbWriter._closed`, `ResourceSampler` | attendre la fin d'un job ; refuser les écritures à la fermeture ; arrêter le thread de mesure |
| `threading.Timer` | `iter_gray_frames` | watchdog : tuer un FFmpeg qui dépasse son délai |
| `multiprocessing.Event` | pool | arrêt de l'application : abandon des segments en cours |
| `multiprocessing.Barrier` | pool | préchauffage : prouver N processus distincts sans `sleep()` |
| `concurrent.futures.wait(FIRST_COMPLETED)` | indexation | fenêtre glissante de segments en vol |

Non utilisés car **inutiles ici** : `RLock` (aucune réentrance), `Semaphore` (les tailles des pools de
threads bornent déjà la concurrence), `multiprocessing.Queue/Pipe` (les futures du
`ProcessPoolExecutor` suffisent), mémoire partagée (on ne partage que des résultats compacts), sockets
(application locale), asyncio (voir § 4).

## 6. Limites du speedup : ce que les mesures montrent

`speedup(N) = T_séquentiel / T_N`, `efficacité(N) = speedup(N) / N`. Rien ne garantit qu'ils soient > 1 :

* **Loi d'Amdahl** : la partie non parallélisée (écriture SQLite séquentielle, démarrage des processus,
  agrégation) borne le gain.
* **Surcoûts** : création des processus (spawn), sérialisation pickle, file inter-processus.
* **Ressources partagées** : disque, cache L3 et surtout **bande passante mémoire**. Décoder de la
  vidéo HD lit/écrit plusieurs Mo par image ; sur un portable à **un seul canal mémoire**, plusieurs
  décodeurs se disputent cette bande passante.
* **Sursouscription** : plus de threads/processus actifs que de cœurs → commutations de contexte.

## 7. Mesures (résumé — détail dans `docs/results/RESULTATS.md`)

Machine : Ryzen 5 5600H (6 cœurs / 12 threads), **8 Go sur un seul canal mémoire**, Windows 11.

| Corpus (345 s de vidéo, 30 segments) | Séquentiel | 2 processus | 4 processus |
|---|---|---|---|
| 1280×720 | 14,59 s | 10,52 s (**×1,39**, eff. 69 %) | 11,02 s (×1,32, eff. 33 %) |
| 480×270 | 3,56 s | 2,00 s (**×1,77**, eff. 89 %) | 1,46 s (**×2,43**, eff. 61 %) |

* Dans **tous** les modes, les empreintes sont strictement identiques à celles du séquentiel, et les PID sont distincts.
* FFmpeg seul, sans Python, montre le même plafond à 720p (×1,11 à 2, ×1,13 à 4 copies) et passe à l'échelle à
  480×270 (×2,61 à 4) : la limite à 720p vient du décodage HD exécuté en plusieurs exemplaires sur cette machine
  (contention mémoire la plus probable), pas du GIL, de l'IPC ni de SQLite.
* Réactivité (version à threads) : pendant l'indexation de deux films sur 2 processus, `GET /api/jobs/{id}`
  répond en **4,2 ms** (médiane ; p95 7 ms ; max 10 ms, 30 mesures) et `GET /api/stats` en 8 ms (médiane).
* Recherche : index par bandes ×2,6 à ×3,0 plus rapide que l'exhaustive entre 50 000 et 500 000 empreintes, avec les
  mêmes verdicts (16/16).
