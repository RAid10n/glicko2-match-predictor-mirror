# AUDIT_CIERRE — Final pre-submission check

- **Date:** 2026-09-25
- **HEAD:** `42fcc63` (working tree clean)
- **Mode:** read-only. Only files and git history were read. No notebook, script or LaTeX build was run. Recomputations were read-only `awk` passes over the CSVs in `Data/`, and they were checked against the notebook's own stored outputs.
- **Compiled document:** `latex/main.tex` pulls in `portada`, `veredicto`, `introduccion`, `marco_teorico`, `metodologia`, `resultados`, `conclusiones` and `appendix`. `antecedentes` is commented out (`main.tex:70`). The eight result tables come in through `\input{figures/tabla_*.tex}` in `resultados.tex`.
- **"Current run":** the corrected-data run of `ModelTraining.ipynb`, started 2026-09-16 20:44 UTC and committed in `238b0d3` (2026-09-17) as "Regenerados 52 artefactos". Its artifacts are `Data/stage2_mlp_results_*.csv`, `Data/stage2_mlp_predictions_*.csv`, `Data/glicko_params_*.json`, `Data/glicko_ratings_final_*.csv` and `Data/tabla_skellam_*.csv`, plus the notebook's Module-4 and Module-7 cell outputs.

## Summary

| # | Item | Verdict |
|---|------|---------|
| 1 | Broken `\ref` / `\eqref` in the compiled document | **PASS** |
| 1b | Table fragments or images on disk that are not linked | Flagged (information only) |
| 2 | `tab:skellam_mae` | **PASS** |
| 2 | `tab:skellam_draw` | **PASS** |
| 2 | `tab:skellam_resid` | **PASS** |
| 2 | `tab:glicko_convergencia` | **FAIL** (mixed: `d` and Evals are current, `R_min`/`R_max`/Rango are from the old run) |
| 2 | `tab:glicko_aislado` | **FAIL** (LL Bin for all datasets and Lite LL Cat are from the old run) |
| 2 | `tab:stage2_resumen` | **FAIL** (old run) |
| 2 | `tab:stage2_f1d` | **FAIL** (old run) |
| 2 | `tab:test_t` | **FAIL** (old run; three significance conclusions change) |
| 3 | Dataset row counts (27,067 / 37,819 / 18,946 / 26,473) | **PASS** |
| 3 | Competition counts (25 Lite / 21 Full) | **FAIL** (`metodologia.tex:319`) |

**Root cause of the Item 2 failures.** Module 4 writes `glicko_params_{tag}.json` with a relative path (`ModelTraining.ipynb`, source line ~4492), and Module 7 writes `stage2_mlp_results_{ds}.csv` with a relative path (`out_csv = f"stage2_mlp_results_{dataset_name}.csv"`, ~5133). Both land in the Colab working directory. The table-generation cells ("CELDA 3/4 RESULTADOS", execution counts 19–21) instead read from `BASE = "/content/drive/MyDrive/TEG02-2025Arellano/Data"`, which still held the previous run's files. Those cells therefore printed and exported **old** `tabla_glicko_*.csv`, `tabla_stage2_*.csv` and `tabla_test_t.csv`. These files were never re-committed after `321f138` (2026-09-10). The fresh run files were later copied into `Data/` (`238b0d3`). The Skellam tables are correct because their cell recomputes from the models it has just trained.

Evidence inside the same notebook:
- Module 7 output: `Test t pareado LL_cat : t=-5.5304, p=0.0004` (Full).
- CELDA 4 output: `Full -7.872023 2.517267e-05`.
- Module 4 output: `d=0.9083`. CELDA "tab:glicko_convergencia" output: `0.908839`.

---

## 1. Broken references

### 1a. `\ref` / `\eqref` resolution — **PASS**

- 137 distinct keys are used through `\ref`, `\eqref`, `\autoref` and `\pageref` in the compiled files (the included chapters plus the eight `figures/tabla_*.tex`).
- 257 distinct `\label`s are defined in those files.
- There are **0 unresolved keys** and **0 duplicate labels**.
- `latex/main.log` (built 2026-09-25 14:44, the same timestamp as the latest table edits) has no "Reference … undefined" or "There were undefined references" warnings.
- None of the referenced keys lives only in the excluded `antecedentes.tex`.

### 1b. The eight result tables

| Label | `\label` defined in | Pulled in by | Referenced at | In compiled doc? |
|---|---|---|---|---|
| `tab:skellam_mae` | `latex/figures/tabla_skellam_mae.tex:4` | `resultados.tex:20` `\input{figures/tabla_skellam_mae.tex}` | `resultados.tex:18` | Yes |
| `tab:skellam_draw` | `latex/figures/tabla_skellam_draw.tex:4` | `resultados.tex:24` | `resultados.tex:22` | Yes |
| `tab:skellam_resid` | `latex/figures/tabla_skellam_resid.tex:4` | `resultados.tex:56` | `resultados.tex:54` | Yes |
| `tab:glicko_convergencia` | `latex/figures/tabla_glicko_convergencia.tex:4` | `resultados.tex:100` | `resultados.tex:98` | Yes |
| `tab:glicko_aislado` | `latex/figures/tabla_glicko_aislado.tex:4` | `resultados.tex:153` | `resultados.tex:151, 243` | Yes |
| `tab:stage2_resumen` | `latex/figures/tabla_stage2_resumen.tex:4` | `resultados.tex:171` | `resultados.tex:169, 177 (×2), 241, 243` | Yes |
| `tab:stage2_f1d` | `latex/figures/tabla_stage2_f1d.tex:4` | `resultados.tex:184` | `resultados.tex:182, 243` | Yes |
| `tab:test_t` | `latex/figures/tabla_test_t.tex:4` | `resultados.tex:199` | `resultados.tex:197, 201` | Yes |

All eight PASS on linkage.

### 1c. Fragments or images on disk that the build does not use (information only)

**Duplicate table fragments in `Data/`, not `\input` anywhere:**
- `Data/tabla_glicko_aislado.tex`, `tabla_glicko_convergencia.tex`, `tabla_skellam_draw.tex`, `tabla_skellam_mae.tex`, `tabla_skellam_resid.tex`, `tabla_stage2_resumen.tex` and `tabla_test_t.tex` carry the **same `\label`s** as the compiled copies.
- They are harmless for compilation, but several differ from the compiled versions:
  - `Data/tabla_glicko_convergencia.tex` shows `d` = 0.9088 / 0.9327.
  - `Data/tabla_glicko_aislado.tex` has no OVR column.
  - `Data/tabla_stage2_resumen.tex` has no OVR column.
- Do not `\input` them by mistake.

**Other unused files:**
- `Data/validaciones_lite.tex` is not input. The appendix defines `tab:validaciones_lite` inline at `appendix.tex:1211`, and the inline rows **differ** from the `Data/` fragment. This table is out of scope; noted only.
- `latex/figures/tabla_stage2_f1d.log` is a stray build artifact.
- `latex/antecedentes.tex` is excluded (`main.tex:70`). None of its labels is referenced.
- Seven images in `latex/figures/` are never `\includegraphics`'d: `correlacionfull.png`, `correlacionlite.png`, `martrizconfusion1.png` (misspelled name), `scatter_AFC_2025.png`, `scatter_ARIMA_Lite.png`, `scatter_LSTM_Lite.png` and `scatter_LSTM_vs_AFC_2026.png`.
- Every `\includegraphics` target exists. `logo_UCV.jpg` is in `latex/`.

---

## 2. Table numbers vs the current run

Notes:
- "Current" values come from the `Data/` artifacts of `238b0d3`, cross-checked against the Module-4 and Module-7 stdout of `ModelTraining.ipynb`.
- Two-sided p-values use a paired t-test with df = 9. My recomputation reproduces the notebook's printed t and p exactly.

### `tab:skellam_mae` — **PASS**

- `latex/figures/tabla_skellam_mae.tex` is byte-identical to the regenerated `Data/tabla_skellam_mae.tex` (`238b0d3`).
- MAE Home / Away:
  - Full: 0.9331 / 0.8375
  - Lite: 0.9283 / 0.8358
  - Lite Rec.: 0.9331 / 0.8375
- N Train / N Test: 18946 / 8121 (Full, Lite Rec.) and 26473 / 11346 (Lite).
- These match `Data/tabla_skellam_mae.csv` (e.g. `full,0.93307…,0.83751…,18946,8121`).
- The old run was `18872/8088` and `26398/11314`. It is gone.

### `tab:skellam_draw` — **PASS**

- The compiled file is identical to `Data/tabla_skellam_draw.tex`.
- Full: 0.5722 / 0.2623 / 0.2567 / −0.0056. Lite: 0.5759 / 0.2666 / 0.2589 / −0.0077.
- `tabla_comparativa_bloques.csv` gives Skellam 0.57218, which is consistent.

### `tab:skellam_resid` — **PASS**

- The only difference from `Data/tabla_skellam_resid.tex` is whitespace.
- Full: 0.001 / 1.171 / 0.034 / 1.089. Lite: 0.003 / 1.167 / 0.030 / 1.085.
- These match `Data/tabla_skellam_resid.csv`.

### `tab:glicko_convergencia` — **FAIL** (mixed provenance)

| Dataset | Column | Table | Current | Source of current | OK? |
|---|---|---|---|---|---|
| Full / Lite Rec. | d | 0.9083 | 0.9083 | `glicko_params_full.json` `"draw_adjustment": 0.908317…`; Module 4 `Eval 660 … d=0.9083` | ✔ |
| Lite | d | 0.9286 | 0.9286 | `glicko_params_lite.json` `0.928629…`; Module 4 `Eval 1440 … d=0.9286` | ✔ |
| Full / Lite Rec. | μ_min | 1172.0 | **1171.1** | min `mu` in `glicko_ratings_final_full.csv` | ✘ |
| Full / Lite Rec. | μ_max | 1807.1 | **1807.3** | max `mu` (PSG 1807.35, as `appendix.tex:509/912` already shows) | ✘ |
| Full / Lite Rec. | Rango | 635.1 | **636.3** | | ✘ |
| Lite | μ_min | 1153.9 | **1151.7** | `glicko_ratings_final_lite.csv` | ✘ |
| Lite | μ_max | 1801.5 | **1801.8** | PSG 1801.79, as `appendix.tex:534/951` shows | ✘ |
| Lite | Rango | 647.6 | **650.1** | | ✘ |
| all | Evals | 660 / 780 / 660 | 660 / 780 / 660 | cumulative counter 1–660, 661–1440, 1441–2100; `metadata.json` | ✔ |

- The rating range comes from the old `Data/tabla_glicko_convergencia.csv` (`321f138`, which also has the old d = 0.9088 / 0.9327). Only `d` was hand-updated.
- This leaves an internal inconsistency: the table says μ_max = 1807.1, but the appendix lists PSG at 1807.35.
- Prose that repeats the stale range: `resultados.tex:102` "Δμ ∈ [635.1, 647.6]". Current is [636.3, 650.1].

### `tab:glicko_aislado` — **FAIL**

| Dataset | Metric | Table | Current (Module 4 stdout) | Recomputed from `stage2_mlp_predictions_*.csv` (glicko_prob_*) | OK? |
|---|---|---|---|---|---|
| Full / Lite Rec. | LL Cat | 1.0425 | 1.042516 → 1.0425 | 1.0426 | ✔ (at 4 dp per notebook) |
| Full / Lite Rec. | LL Bin | 0.6500 | **0.649586 → 0.6496** | 0.6494 | ✘ |
| Full / Lite Rec. | LL OVR | 0.6064 | — | 0.6064 | ✔ |
| Lite | LL Cat | 1.0414 | **1.041157 → 1.0412** | 1.0412 | ✘ |
| Lite | LL Bin | 0.6478 | **0.647266 → 0.6473** | 0.6473 | ✘ |
| Lite | LL OVR | 0.6058 | — | 0.6058 | ✔ |

- Table values = old `Data/tabla_glicko_aislado.csv` (`1.04249…, 0.64997…`; `1.04139…, 0.64782…`), i.e. the notebook CELDA 3 output that read old predictions from `BASE`.
- The same stale values are repeated in:
  - `resultados.tex:155` ("1.0414", "1.0425")
  - `resultados.tex:230` (`tab:comparativa_bloques`: "1.0425 & 0.6500")
  - `appendix.tex:158, 162, 166, 173` ("1.0414 & 0.6478", "0.6478–0.6500")

### `tab:stage2_resumen` — **FAIL**

Table (= old `Data/tabla_stage2_resumen.csv`, `321f138`) vs current (`Data/stage2_mlp_results_*.csv` plus OVR from `stage2_mlp_predictions_*.csv`):

| Dataset | Config | LL Cat. table → current | LL Bin. table → current | Acc. | F1_D | Prec_D | LL OVR |
|---|---|---|---|---|---|---|---|
| Full | Base | 1.0247±0.0002 → **1.0248±0.0001** | 0.6449±0.0002 → **0.6443±0.0001** | 0.486 → **0.485** | 0.0000 ✔ | 0.0 ✔ | 0.5975 ✔ |
| Full | Completo | 1.0261±0.0004 → 1.0261±**0.0007** | 0.6445±0.0003 → **0.6440±0.0005** | 0.489 ✔ | 0.0033 → **0.0043** | 0.162 → **0.206** | 0.5978 → **0.5983** |
| Lite | Base | 1.0230±0.0002 → **1.0227±0.0002** | 0.6417±0.0001 → **0.6410±0.0002** | 0.487 ✔ | 0.0000 ✔ | 0.0 ✔ | 0.5964 ✔ |
| Lite | Completo | 1.0224±0.0006 → **1.0226±0.0002** | 0.6402±0.0005 → **0.6401±0.0003** | 0.488 → **0.489** | 0.0063 → **0.0075** | 0.276 → **0.270** | 0.5959 → **0.5963** |
| Lite Rec. | Base | = Full Base (same changes as Full Base) | | 0.486 → **0.485** | 0.0000 ✔ | 0.0 ✔ | 0.5975 ✔ |
| Lite Rec. | Completo | 1.0273±0.0005 → **1.0276±0.0005** | 0.6460±0.0005 → **0.6457±0.0003** | 0.486 ✔ | 0.0003 → **0.0015** | 0.035 → **0.214** | 0.5989 → **0.5992** |

Module 7 stdout confirms the current means, e.g. "RESUMEN STAGE2 — full":
- LogLoss Cat 1.024793 / 1.026070
- LogLoss Bin 0.644344 / 0.644021
- F1 Draw 0.0000 / 0.0043

**Qualitative change.** In Lite, Completo is now only 0.0001 better than Base on LL Cat (it used to be 0.0006), and the difference is not significant (see `tab:test_t`).

Prose and tables that repeat stage-2 numbers from the old run:
- `resultados.tex:175`: "1.0230 a 1.0224 (Δ=−0.0006, p=0.0292)", "p=0.0057", "1.0247 a 1.0273", "0.6449 a 0.6460".
- `resultados.tex:177`: accuracies "0.486 y 0.489 … 0.487 y 0.488 … 0.486".
- `resultados.tex:188, 190, 192`: F1_D 0.0033 / 0.0063 / 0.0003 and Prec_D 0.276 / 0.162 / 0.035.
- `resultados.tex:232`: `tab:comparativa_bloques` MLP completo OVR 0.5978.
- `resultados.tex:241`: "Δ=+0.0014 … Δ=+0.0003 … Δ=−0.0004, p=0.0057 … Δ=−0.0006 … Δ=−0.0015, ambos con p<0.05".
- `resultados.tex:243`: "1.0273", "0.0033 … 0.0003 … un orden de magnitud". Current is 0.0043 vs 0.0015, about ×3, **not** an order of magnitude.
- `resultados.tex:245, 264` (`tab:comparativa_ovr`) and `conclusiones.tex:20`: "0.5959". Current is **0.5963**. The gaps become 0.0014 vs canonical 0.5949 (text: 0.0010) and 0.0131 vs 0.5832 (text: 0.0127; also `conclusiones.tex:34`).
- `conclusiones.tex:16`: "desciende entre 0.0152 y 0.0190". Current is roughly 0.0149–0.0185.
- `appendix.tex:163, 167`: "1.0224 & 0.6402 & 0.488" and "1.0273 & 0.6460 & 0.486".

### `tab:stage2_f1d` — **FAIL**

- The table matches old `Data/tabla_stage2_f1d.csv` (e.g. Full run 1 = 0.000, run 3 = 0.007).
- Current `f1_D_full` per run from `stage2_mlp_results_*.csv` / Module 7 stdout:
  - Full: 0.004 0.006 0.014 0.004 0.003 0.000 0.007 0.002 0.001 0.003. Mean **0.0043** (table: 0.0033).
  - Lite: 0.006 0.007 0.004 0.007 0.017 0.005 0.007 0.008 0.013 0.002. Mean **0.0075** (table: 0.0063).
  - Lite Rec.: 0.000 0.004 0.002 0.000 0.001 0.004 0.003 0.000 0.000 0.002. Mean **0.0015** (table: 0.0003).
- Base is 0.000 for every run in all datasets ✔.

### `tab:test_t` — **FAIL** (highest priority)

| Dataset | Metric | Table t / p | Current t / p (Module 7 stdout = recomputed) | Significance at 5 % |
|---|---|---|---|---|
| Full | LL Cat | −7.872 / <0.0001 | **−5.530 / 0.0004** | unchanged (sig.) |
| Full | LL Bin | 3.603 / 0.0057 | **1.883 / 0.0924** | **FLIPS → not significant** |
| Full | F1_D | −4.146 / 0.0025 | **−3.415 / 0.0077** | unchanged (sig.) |
| Lite | LL Cat | 2.590 / 0.0292 | **1.044 / 0.3237** | **FLIPS → not significant** |
| Lite | LL Bin | 9.185 / <0.0001 | **10.654 / <0.0001** (2.1e-6) | unchanged |
| Lite | F1_D | −14.495 / <0.0001 | **−5.541 / 0.0004** | unchanged |
| Lite Rec. | LL Cat | −12.831 / <0.0001 | **−15.246 / <0.0001** (9.8e-8) | unchanged |
| Lite Rec. | LL Bin | −7.036 / <0.0001 | **−9.951 / <0.0001** (3.7e-6) | unchanged |
| Lite Rec. | F1_D | −1.406 / 0.1933 | **−3.077 / 0.0132** | **FLIPS → significant** |

- Table values = old `Data/tabla_test_t.csv` (`321f138`) = the stale notebook CELDA 4 output.
- The notebook's own Module 7 prints the current values: `Test t pareado LL_cat : t=-5.5304, p=0.0004`, `LL_bin : t=1.8829, p=0.0924`, and so on.

Conclusions in the text that no longer hold under the current run:
- `resultados.tex:175, 241`: "Full … mejora marginalmente la binaria (p=0.0057)". Now p = 0.092, so there is no significant improvement.
- `resultados.tex:175, 241`: "Lite … mejora ambas log-loss (… ambos con p<0.05)". LL Cat now has p = 0.32.
- `resultados.tex:190`: "Lite Recortado el F1_D se mantiene prácticamente nulo (0.0003, p=0.1933)". Now 0.0015 with p = 0.013.
- `conclusiones.tex:16`: "significativa en el Dataset Full y el Dataset Lite" (now also in Lite Rec.), "en el Dataset Lite mejora ambas log-loss" and "en el Dataset Full mejora solo la binaria". Both of the last two lose significance.
- `conclusiones.tex:32` / `resultados.tex:243`: the narrative "no recuperan la clase empate" for Lite Rec. is contradicted.

**Fix path (not applied — read-only).**
1. In `ModelTraining.ipynb`, write Module 4 and Module 7 outputs to `os.path.join(BASE_DATA_DIR, …)`, or point the CELDA 3/4 readers at the same directory.
2. Re-run CELDAs 19–24 (or the whole notebook) and re-export `tabla_glicko_*`, `tabla_stage2_*` and `tabla_test_t`.
3. Replace the four FAIL fragments in `latex/figures/`, then update the prose and the dependent tables (`tab:comparativa_bloques`, `tab:comparativa_ovr`, appendix `tab:gru_test` block at `appendix.tex:153–167`) listed above.

---

## 3. Dataset / competition consistency

### 3a. Row counts — **PASS**

| Value | Occurrences |
|---|---|
| 27,067 (Full / Lite Rec.) | `metodologia.tex:256, 260, 308, 309, 315, 317`; `resultados.tex:4` (×2); `appendix.tex:274, 275, 374` |
| 37,819 (Lite) | `metodologia.tex:91, 258, 260, 307, 315, 334`; `resultados.tex:4`; `appendix.tex:273` |
| 18,946 train (Full) | `resultados.tex:155, 243`; `conclusiones.tex:18`; `figures/tabla_skellam_mae.tex:9, 11` |
| 26,473 train (Lite) | `resultados.tex:155, 243, 262–264`; `conclusiones.tex:18, 20, 34`; `figures/tabla_skellam_mae.tex:10` |

- These agree with `Data/tabla_skellam_mae.csv`: 18946 + 8121 = 27,067 and 26473 + 11346 = 37,819.
- **Leftovers:** a search for 26,961, 37,712, 18,872, 26,398, 8,088 and 11,314 (all spacing variants) returns **0 hits**.
- `introduccion.tex` states no row counts, so there is nothing to conflict with.

### 3b. Competition counts — **FAIL**

Consistent occurrences:
- 25 (Lite): `introduccion.tex:13`, `metodologia.tex:18`, `metodologia.tex:109`, `appendix.tex:273`, `conclusiones.tex:14`.
- 21 (Full / Lite Rec.): `metodologia.tex:319` ("reduce el universo a 21"), `appendix.tex:274`, `conclusiones.tex:14`.
- `resultados.tex` states no competition count (`:98` only says "mayor número de competiciones").

The artifacts confirm the counts: `glicko_params_lite.json` → `mu_init_by_league` has 25 keys, and `glicko_params_full.json` has 21.

Defects at `metodologia.tex:319`:
1. **Truncated number.** The sentence reads "El universo de competiciones consideradas es de **2**." Git history shows commit `93c8f5c` (2026-09-24) replaced the leftover "veinticuatro" with "2" instead of "25" / "veinticinco". It will compile and print "2".
2. **Exclusion arithmetic does not add up.** The text says three competitions were excluded (Persian Gulf Pro League, Liga FutVe, AFC Champions League Two): 25 − 3 = 22, not 21. `glicko_params_full.json` actually lacks **four** Lite competitions: `AFCChampionsLeagueElite`, `AFCChampionsTwo`, `FutVe` and `PersianGulfProLeague`. The AFC Champions League Elite exclusion and its reason are undocumented.
3. **List vs count.** The list at `metodologia.tex:20–26` has 24 bullet entries. It reaches 25 only because "Liga Profesional / Superliga (Argentina)" counts as two (`LigaProfesionalArgentina` and `SuperligaArgentina` are separate keys in the JSON). This is the likely origin of the old "veinticuatro" (24). Consider making it explicit.

No other leftover 23 or 24 is used as a competition count. The only other "23" is `resultados.tex:102` "h_ℓ=23 puntos", which is unrelated.
