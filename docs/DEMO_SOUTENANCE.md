# Démonstration de soutenance — MovieShazam (un seul PC)

Durée visée : **10 à 12 minutes**. Tout se passe sur le PC de présentation, sans réseau.
Chaque étape indique *quoi faire*, *quoi montrer* et *quoi dire*.

## 0. Préparation (la veille, puis 10 min avant)

```powershell
# une seule fois : installe .venv, dépendances exactes, build du frontend
powershell -ExecutionPolicy Bypass -File scripts\start_windows.ps1 -SetupOnly
# corpus de démonstration (≈ 2 min, déterministe)
.venv\Scripts\python scripts\generate_demo_videos.py
# vérifier que tout est vert (≈ 1 min)
powershell -ExecutionPolicy Bypass -File scripts\check_quality.ps1
```

10 minutes avant : fermer les applications lourdes (le PC de mesure n'a que 8 Go de RAM),
brancher le secteur, lancer `scripts\start_windows.ps1`. Garder ouverts : l'explorateur sur
`data\demo\clips`, un terminal dans `backend\`, et `docs\results\`.

Pour repartir d'une bibliothèque vide : arrêter l'application puis renommer `data\movieshazam.db`
et `data\media` (ne jamais supprimer de vidéos personnelles : seul le stockage de l'application est concerné).

## 1. Le problème (1 min) — page *Tableau de bord*

* « J'ai 10 secondes d'une vidéo : de quel film viennent-elles, et à quel instant ? »
* Montrer la barre latérale : *Moteur local opérationnel*, **2 processus CPU** avec leurs **PID réels**,
  2 threads E/S, index `phash-v1@2fps`. Insister : 127.0.0.1, aucun service distant, aucun GPU.

## 2. Indexation réelle de 4 films (2 min) — page *Bibliothèque*

1. Glisser-déposer les 4 fichiers de `data\demo\movies\` (films synthétiques générés par nos soins, 1280×720).
2. Montrer la progression **réelle** (segments terminés / total) pendant l'envoi puis l'indexation.
3. Ouvrir un film → **carte des segments** : chaque couleur = le PID du processus qui a calculé le segment.
   Dire : « une tâche = un segment, jamais une image ; les processus renvoient seulement des hashes
   et des timestamps ; un seul thread écrit dans SQLite. »
4. Réimporter le même fichier → message « déjà indexé : aucun recalcul » (empreinte SHA-256).

## 3. Clip connu de 10 s (1 min 30) — page *Reconnaissance*

* Déposer `tes_A_044600_10s_clean.mp4` (commence à 44,6 s et **traverse une frontière de segment**).
* Commenter les étapes réelles : validation → empreintes du clip → candidats → alignement → résultat (durées affichées).
* Résultat attendu : **Océan de Verre, de 00:00:44 à 00:00:54**, score ≈ 0,98, couverture 100 %,
  20/20 images cohérentes. Montrer la frise (position dans le film), les traits colorés (distance de
  Hamming par image alignée), le lecteur positionné au bon instant.
* Dire : « le score est un indicateur heuristique, pas une probabilité ; les seuils ont été fixés sur un
  jeu de validation distinct du test. »

## 4. Recompression et redimensionnement (1 min)

* `tes_A_080250_10s_combo.mp4` : 480 px de large + 15 img/s + CRF 30 → toujours trouvé (score ≈ 0,84).
* `tes_C_024500_10s_clean.mp4` : **scène sombre** → trouvée (le pHash ignore la luminosité moyenne).

## 5. Refus honnêtes (1 min 30)

* `tes_F_003000_10s_clean.mp4` (film jamais indexé) → **« Aucune correspondance suffisamment fiable »**.
* `tes_F_000000_8s_combo.mp4` : contient le **logo commun** à tous les films → quelques images ressemblent
  à 4 films, mais la cohérence temporelle échoue (couverture ≈ 23 %) → refus.
* `val_D_020500_7s_clean.mp4` : passage **présent dans deux films** → **« Résultat ambigu »** avec les deux
  placements (Forêt Électrique 00:00:40 et Ville Fractale 00:00:20). « Nous préférons signaler
  l'ambiguïté plutôt que d'inventer un gagnant. »

## 6. Séquentiel vs parallèle (2 min) — page *Performances*

* Montrer le dernier benchmark (ou en lancer un court : séquentiel + 2 processus, 1 répétition) :
  speedup, efficacité, images/s, RAM, **PID distincts**, colonne « empreintes identiques : oui ».
* Expliquer le résultat honnêtement (chiffres dans `docs/results/RESULTATS.md`) : à 720p, le décodage
  sature la **bande passante mémoire** de ce portable (un seul canal mémoire) → speedup modeste et
  4 processus pas meilleurs que 2 ; sur le même corpus en 480×270 (limité par le CPU), le speedup
  atteint ≈ 2,5 avec 4 processus. Montrer la courbe de rappel de l'index par bandes et le passage à
  l'échelle bandes vs exhaustive.

## 7. Réactivité, annulation, échec (1 min 30) — page *Traitements*

* Pendant une indexation (« Tout recalculer » sur un film) : naviguer dans l'interface → elle reste fluide
  (le polling HTTP répond en quelques ms : chaque requête est servie par un thread, le calcul est dans les processus).
* Cliquer **Annuler** : les segments non démarrés sont annulés, ceux en cours se terminent ; le film passe
  en « Annulé » ; « Réindexer » ne recalcule que les segments manquants (`segments réutilisés`).
* Option : importer un MP4 tronqué → job **« Échec »** avec un message explicite, l'API reste disponible,
  la base n'est pas corrompue.

## 8. Tests automatisés réellement exécutés (1 min) — terminal

```powershell
cd backend
..\.venv\Scripts\python -m pytest -q -m "not slow and not e2e"   # ≈ 25 s
..\.venv\Scripts\python -m pytest -q -k "race or identical"      # course + séquentiel == parallèle
```

Montrer `test_race_condition_without_lock_loses_updates` (perte réelle d'incréments sans verrou) et
`test_single_writer_serializes_concurrent_sqlite_updates` (0 perte avec l'écrivain unique), puis
`test_sequential_and_parallel_produce_identical_index` (mêmes empreintes, 2 PID distincts).

## Plan B (si quelque chose ne marche pas)

* Backend injoignable : regarder la fenêtre « MovieShazam - backend » ; FFmpeg absent → `winget install Gyan.FFmpeg`.
* Port occupé : une instance tourne déjà → fermer ses fenêtres ou redémarrer.
* Montrer les rapports déjà produits : `docs/results/eval_test.md`, `docs/results/RESULTATS.md`, `docs/screenshots/`.
