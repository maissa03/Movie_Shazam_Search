### Split `validation` — méthode `bands` : 12/12 corrects (erreur d'horodatage moyenne 0.0 ms, max 0 ms ; recherche moyenne 338.6 ms)

| Clip | Transformation | Attendu | Obtenu | Erreur (ms) | Score meilleur | 2e candidat | Remarque |
|---|---|---|---|---|---|---|---|
| val_A_007000_10s_clean | clean | match | ✅ match | 0 | A@7.0s 1.000 | — |  |
| val_A_063300_10s_recompress | recompress | match | ✅ match | 0 | A@63.3s 0.830 | — | début non aligné |
| val_B_012700_5s_clean | clean | match | ✅ match | 0 | B@12.7s 0.923 | — | clip court 5 s |
| val_B_055000_20s_downscale | downscale | match | ✅ match | 0 | B@55.0s 0.962 | C@11.4s 0.011 |  |
| val_C_036400_10s_fps15 | fps15 | match | ✅ match | 0 | C@36.4s 0.946 | — |  |
| val_C_060000_10s_combo | combo | match | ✅ match | 0 | C@60.0s 0.908 | — |  |
| val_D_035200_10s_clean | clean | match | ✅ match | 0 | D@35.2s 0.992 | D@31.6s 0.050 |  |
| val_D_044000_12s_recompress | recompress | match | ✅ match | 0 | D@44.0s 0.962 | — |  |
| val_D_020500_7s_clean | clean | ambiguous | ✅ ambiguous | — | B@40.5s 0.949 | D@20.5s 0.949 | passage partagé D[20-28]=B[40-48] |
| val_E_000500_10s_clean | clean | no_match | ✅ no_match | — | A@0.5s 0.146 | B@0.5s 0.146 | film non indexé (contient le logo commun) |
| val_E_018200_5s_recompress | recompress | no_match | ✅ no_match | — | — | — | film non indexé |
| val_E_009000_15s_downscale | downscale | no_match | ✅ no_match | — | — | — | film non indexé |

### Split `validation` — méthode `exhaustive` : 12/12 corrects (erreur d'horodatage moyenne 0.0 ms, max 0 ms ; recherche moyenne 303.0 ms)

| Clip | Transformation | Attendu | Obtenu | Erreur (ms) | Score meilleur | 2e candidat | Remarque |
|---|---|---|---|---|---|---|---|
| val_A_007000_10s_clean | clean | match | ✅ match | 0 | A@7.0s 1.000 | — |  |
| val_A_063300_10s_recompress | recompress | match | ✅ match | 0 | A@63.3s 0.830 | B@83.6s 0.004 | début non aligné |
| val_B_012700_5s_clean | clean | match | ✅ match | 0 | B@12.7s 0.923 | — | clip court 5 s |
| val_B_055000_20s_downscale | downscale | match | ✅ match | 0 | B@55.0s 0.962 | C@11.4s 0.011 |  |
| val_C_036400_10s_fps15 | fps15 | match | ✅ match | 0 | C@36.4s 0.946 | — |  |
| val_C_060000_10s_combo | combo | match | ✅ match | 0 | C@60.0s 0.908 | — |  |
| val_D_035200_10s_clean | clean | match | ✅ match | 0 | D@35.2s 0.992 | D@31.6s 0.050 |  |
| val_D_044000_12s_recompress | recompress | match | ✅ match | 0 | D@44.0s 0.962 | — |  |
| val_D_020500_7s_clean | clean | ambiguous | ✅ ambiguous | — | B@40.5s 0.949 | D@20.5s 0.949 | passage partagé D[20-28]=B[40-48] |
| val_E_000500_10s_clean | clean | no_match | ✅ no_match | — | A@0.5s 0.146 | B@0.5s 0.146 | film non indexé (contient le logo commun) |
| val_E_018200_5s_recompress | recompress | no_match | ✅ no_match | — | — | — | film non indexé |
| val_E_009000_15s_downscale | downscale | no_match | ✅ no_match | — | — | — | film non indexé |
