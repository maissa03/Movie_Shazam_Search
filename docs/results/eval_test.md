### Split `test` — méthode `bands` : 16/16 corrects (erreur d'horodatage moyenne 8.3 ms, max 50 ms ; recherche moyenne 369.4 ms)

| Clip | Transformation | Attendu | Obtenu | Erreur (ms) | Score meilleur | 2e candidat | Remarque |
|---|---|---|---|---|---|---|---|
| tes_A_000000_10s_clean | clean | match | ✅ match | 0 | A@0.0s 0.967 | B@0.2s 0.158 | début du film (noir + logo commun) |
| tes_A_044600_10s_clean | clean | match | ✅ match | 0 | A@44.6s 0.985 | — | traverse une frontière de segment (45 s) |
| tes_A_105000_15s_clean | clean | match | ✅ match | 0 | A@105.0s 0.980 | C@60.0s 0.185 | fin du film (générique) |
| tes_A_080250_10s_combo | combo | match | ✅ match | 50 | A@80.3s 0.836 | A@76.4s 0.099 | 480x270 + 15 fps + CRF 30 |
| tes_A_030500_5s_clean | clean | ambiguous | ✅ ambiguous | — | A@30.5s 0.831 | A@90.5s 0.831 | scène répétée A[30-36]=A[90-96] |
| tes_B_003000_5s_clean | clean | match | ✅ match | 0 | B@3.0s 0.939 | — |  |
| tes_B_070300_10s_recompress | recompress | match | ✅ match | 0 | B@70.3s 0.911 | — |  |
| tes_B_076000_14s_downscale | downscale | match | ✅ match | 0 | B@76.0s 0.962 | D@46.0s 0.133 | fin du film |
| tes_C_024500_10s_clean | clean | match | ✅ match | 0 | C@24.5s 0.906 | C@53.0s 0.004 | scène sombre |
| tes_C_025000_6s_recompress | recompress | match | ✅ match | 0 | C@25.0s 0.667 | C@53.0s 0.006 | scène sombre recompressée |
| tes_C_050750_20s_downscale | downscale | match | ✅ match | 50 | C@50.8s 0.812 | B@18.8s 0.006 | traverse 2 frontières de segment |
| tes_D_002000_10s_fps30 | fps30 | match | ✅ match | 0 | D@2.0s 0.985 | A@1.5s 0.146 | FPS 25 -> 30 |
| tes_D_018000_15s_clean | clean | match | ✅ match | 0 | D@18.0s 0.990 | B@38.0s 0.558 | contient le passage partagé + contexte propre à D |
| tes_F_003000_10s_clean | clean | no_match | ✅ no_match | — | A@38.9s 0.004 | — | film non indexé |
| tes_F_015500_10s_recompress | recompress | no_match | ✅ no_match | — | — | — | film non indexé |
| tes_F_000000_8s_combo | combo | no_match | ✅ no_match | — | A@0.0s 0.225 | B@0.0s 0.225 | film non indexé (noir + logo commun) |

### Split `test` — méthode `exhaustive` : 16/16 corrects (erreur d'horodatage moyenne 8.3 ms, max 50 ms ; recherche moyenne 331.0 ms)

| Clip | Transformation | Attendu | Obtenu | Erreur (ms) | Score meilleur | 2e candidat | Remarque |
|---|---|---|---|---|---|---|---|
| tes_A_000000_10s_clean | clean | match | ✅ match | 0 | A@0.0s 0.967 | B@0.2s 0.158 | début du film (noir + logo commun) |
| tes_A_044600_10s_clean | clean | match | ✅ match | 0 | A@44.6s 0.985 | — | traverse une frontière de segment (45 s) |
| tes_A_105000_15s_clean | clean | match | ✅ match | 0 | A@105.0s 0.980 | C@60.0s 0.185 | fin du film (générique) |
| tes_A_080250_10s_combo | combo | match | ✅ match | 50 | A@80.3s 0.836 | A@76.4s 0.099 | 480x270 + 15 fps + CRF 30 |
| tes_A_030500_5s_clean | clean | ambiguous | ✅ ambiguous | — | A@30.5s 0.831 | A@90.5s 0.831 | scène répétée A[30-36]=A[90-96] |
| tes_B_003000_5s_clean | clean | match | ✅ match | 0 | B@3.0s 0.939 | — |  |
| tes_B_070300_10s_recompress | recompress | match | ✅ match | 0 | B@70.3s 0.911 | — |  |
| tes_B_076000_14s_downscale | downscale | match | ✅ match | 0 | B@76.0s 0.962 | D@46.0s 0.133 | fin du film |
| tes_C_024500_10s_clean | clean | match | ✅ match | 0 | C@24.5s 0.906 | C@53.0s 0.004 | scène sombre |
| tes_C_025000_6s_recompress | recompress | match | ✅ match | 0 | C@25.0s 0.667 | C@52.9s 0.006 | scène sombre recompressée |
| tes_C_050750_20s_downscale | downscale | match | ✅ match | 50 | C@50.8s 0.812 | B@18.8s 0.006 | traverse 2 frontières de segment |
| tes_D_002000_10s_fps30 | fps30 | match | ✅ match | 0 | D@2.0s 0.985 | A@1.5s 0.146 | FPS 25 -> 30 |
| tes_D_018000_15s_clean | clean | match | ✅ match | 0 | D@18.0s 0.990 | B@38.0s 0.558 | contient le passage partagé + contexte propre à D |
| tes_F_003000_10s_clean | clean | no_match | ✅ no_match | — | A@38.9s 0.004 | — | film non indexé |
| tes_F_015500_10s_recompress | recompress | no_match | ✅ no_match | — | A@111.0s 0.004 | — | film non indexé |
| tes_F_000000_8s_combo | combo | no_match | ✅ no_match | — | A@0.0s 0.225 | B@0.0s 0.225 | film non indexé (noir + logo commun) |
