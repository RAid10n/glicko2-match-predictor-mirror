# glicko2-match-predictor

Trabajo Especial de Grado (TEG) — modelo de nivel de poder para ligas de fútbol que integra rendimiento deportivo, factores económicos y factores ambientales, sobre un motor Glicko-2 modificado (siguiendo como base el paper de Shelopugin & Sirotkin, 2023). Cobertura: 15 ligas nacionales, temporadas 2017-18 a 2024-25.

## Clonar el repositorio

`Data/matches_definitive_complete.csv` (~64 MB) se versiona con **Git LFS**, no directamente en git. Para clonar el repo con ese archivo incluido:

```bash
# 1. Instalar Git LFS una sola vez por máquina (si no lo tienes)
git lfs install

# 2. Clonar normalmente — LFS descarga el CSV real automáticamente
git clone https://github.com/maidatas/glicko2-match-predictor.git
```

Si ya clonaste el repo antes de tener `git-lfs` instalado, el archivo va a aparecer como un puntero de texto (unas pocas líneas con `oid sha256:...`) en vez del CSV real. Para corregirlo:

```bash
git lfs install
git lfs pull
```

## Estructura del repositorio

```
glicko2-match-predictor/
├── G2Av1513.py                          # Motor Glicko-2 modificado vigente
├── ModelTraining.ipynb                  # Entrenamiento: Skellam/LGBM + Glicko + Stage 2 MLP
│
├── Data/
│   │
│   ├── open_meteo_fetch.py              # Scraper Open-Meteo
│   │
│   ├── Carga.ipynb                      # Etapa 1
│   ├── Limpieza.ipynb                   # Etapa 2
│   ├── MacroEconomyEst.ipynb            # Imputación macro (input de Limpieza)
│   ├── Extra2.ipynb                     # Etapa 4: proyección ARIMA/LSTM
│   ├── Extra1.ipynb                     # Etapa 5: baseline Naive + validaciones
│   │
│   ├── EstadiumsDB.csv                  # [input] estadios crudos
│   ├── EstadiumsDB_normalized.csv       # [input] estadios curados
│   ├── team_name_review.csv             # [input] normalización manual de nombres
│   ├── MacroEconomy2_imputado.csv       # [input] macro por país/año
│   ├── TeamsEconomyDB.csv               # [input] economía por club/semestre
│   ├── PartidosExtra.csv                # [input] partidos cargados a mano
│   ├── uefa_ranking_2025.csv            # [input] ranking UEFA
│   ├── uefa_ranking_2026.csv
│   ├── RankingAFC2025.csv               # [input] ranking AFC
│   ├── RankingAFC2026.csv
│   ├── conmebol_final.csv               # [input] ranking CONMEBOL
│   ├── Ranking-de-Clubes-CONMEBOL-2026-1.pdf
│   ├── ELO20250802.txt                  # [input] ELO ago-2025
│   ├── ELO20260902.txt                  # [input] ELO sep-2026
│   │
│   ├── db_matches_final.csv             # [Carga → Limpieza] versión plana
│   ├── db_matches_final_home.csv        # [Carga → Limpieza] operativo local
│   ├── db_matches_final_away.csv        # [Carga → Limpieza] operativo visitante
│   ├── matches_final_model_full2.csv    # [Limpieza → ModelTraining, Extra1]  27.067 filas
│   ├── matches_final_model_clean2.csv   # [Limpieza → ModelTraining, Extra1]  37.819 filas
│   ├── glicko_ratings_history_full.csv  # [ModelTraining → Extra2]
│   ├── glicko_ratings_history_lite.csv  # [ModelTraining → Extra2]
│   ├── glicko_ratings_final_full.csv    # [ModelTraining → Extra1]
│   ├── glicko_ratings_final_lite.csv    # [ModelTraining → Extra1]
│   ├── glicko_ratings_final_lite_recortado.csv  # [ModelTraining → Extra1]
│   ├── forecast_arima_52w_full.csv      # [Extra2 → Extra1]
│   ├── forecast_arima_52w_lite.csv      # [Extra2 → Extra1]
│   ├── forecast_lstm_52w_full.csv       # [Extra2 → Extra1]
│   ├── forecast_lstm_52w_lite.csv       # [Extra2 → Extra1]
│   │
│   ├── open_meteo_out/                  # ~437 CSVs de clima histórico por equipo
│   ├── Legacy ipynb py/                 # Historial: versiones anteriores del motor
│   ├── Legacy Data/                     # Historial: datasets deprecados
│   └── GlickoSoccer-master/             # Repo de referencia externo
│
└── Extras/
    └── GlickoSoccerMaster/              # Copia del repo de referencia + paper PDF
```

## Convención de nombres de versión

Los scripts del motor siguen el patrón **`G2Av{x}{m}{dd}`**:

- **`G2Av{x}{m}{dd}.py`** — motor del modelo: implementa las funciones de probabilidad (victoria local, derrota, empate vía la función de Skellam) sobre el esquema Glicko-2 modificado.
- **`x`** = versión del modelo · **`m`** = mes de creación · **`dd`** = día de la primera prueba de esa revisión.

El motor vigente en la raíz es `G2Av1513.py` (versión 1, mes 5, día 13). Versiones anteriores (`G2Av1216`, `G2Av1226`, `G2Av1427`, y sus `Master*` correspondientes) se conservan en `Data/Legacy ipynb py/` y `Data/Legacy Data/` como historial de iteración.

## Datos (`Data/`)

Cobertura temporal de todos los datasets: **agosto 2017 – agosto 2025**.

| Archivo | Contenido | Fuente |
|---|---|---|
| `EstadiumsDB.csv` | País y equipo del recinto, id, coordenadas (lat/lon), capacidad, altura (m), asistencia promedio anual. Temporadas de invierno usan la nomenclatura de archivo completa; temporadas anuales usan solo los últimos dos dígitos. | Asistencia: promedios anuales de transfermarkt.com |
| `MacroEconomy2_imputado.csv` | PIB, inflación, índice de inversión extranjera (IED), población total, índice Gini, gasto público y deuda externa por país, medidos anualmente (2017-2025), con imputación de valores faltantes (spline/ARIMA/auto-ARIMA/MICE). | datosmacro.expansion.com |
| `TeamsEconomyDB.csv` | País, año, semestre, equipo, cantidad de jugadores totales y extranjeros en la plantilla senior, valor de mercado, gastos e ingresos por fichajes (en millones de euros), medidos semestralmente, solo primera división de los países del estudio. | transfermarkt.com |
| `db_matches_final_home.csv` | Métricas deportivas por partido y equipo en condición de localía: asistencias, tarjetas, centros, faltas (cometidas/recibidas), goles, intercepciones, autogoles, fuera de juego, penales (goles/intentos/concedidos/ganados), tiros (totales/al arco), duelos ganados, fecha, equipos local/visitante, competencia. Solo primera división de los países del estudio. | FBRef.com |
| `db_matches_final_away.csv` | Métricas deportivas por partido y equipo en condición de visita: asistencias, tarjetas, centros, faltas (cometidas/recibidas), goles, intercepciones, autogoles, fuera de juego, penales (goles/intentos/concedidos/ganados), tiros (totales/al arco), duelos ganados, fecha, equipos local/visitante, competencia. Solo primera división de los países del estudio. | FBRef.com |

### Scrapers de datos deportivos

Versiones sucesivas conservadas en `Data/Legacy ipynb py/` y `Data/ScraperV4c.py`:

| Script | Descripción |
|---|---|
| `scrapper_fbref.py` | Primera versión del web scraper |
| `scrapper_fbref_invierno.py` | Variante para ligas de temporada anual (invierno-verano) |
| `scrapper_fbref_verano.py` | Variante para ligas que inician en agosto |
| `ScrapperV2.py` | Segunda versión; agrega headers para pasar la revisión de seguridad de Cloudflare tras su primera caída mundial |
| `ScraperV4.py` | Evolución de la V2 (nombrada "V4" por error de numeración); actualmente rota, no funciona |
| `ScraperV3.5.py` | Actualización de la V4 con headers dinámicos y Selenium para sortear Cloudflare tras su segunda caída mundial |
| `ScrapperV4aAlpha.py` | Pruebas de nuevas características sobre la V3.5; actualmente rota |
| `ScraperV4a.py` | Versión final; añade extracción de cookies para mejor uso de Selenium |
| `ScraperV4b.py` | Variante de V4a; busca las ligas por coincidencia de nombre en vez de por id |
| `ScraperV4c.py` (raíz de `Data/`) | Versión vigente usada para la consolidación actual |

## Notebooks de limpieza y preparación (`Data/`)

- **`Carga.ipynb`** — carga y consolidación inicial de los datos crudos scrapeados de FBRef. Normaliza los nombres de columnas, filtra por las 15 ligas nacionales objetivo y torneos continentales asociados, y depura los partidos de fútbol femenino por `team_id` (combinando listas de equipos conocidos y detección heurística por nombre). Separa los datos en dos datasets operativos: local (`db_matches_final_home.csv`) y visitante (`db_matches_final_away.csv`), más una versión plana (`db_matches_final.csv`) para auditoría.
- **`Limpieza.ipynb`** — integra variables climáticas (Open-Meteo, agregadas por equipo/mes/año desde `open_meteo_out/`), variables económicas de club (`TeamsEconomyDB`) y macro (`MacroEconomy2_imputado`); calcula las features de diferencia (`diff_*`) usadas por el modelo; verifica que no queden partidos femeninos tras el filtrado de `Carga`; y exporta los datasets finales: `matches_final_model_full2.csv` (27.067 filas) y `matches_final_model_clean2.csv` (37.819 filas).
- **`MacroEconomyEst.ipynb`** — imputa los valores faltantes de las variables macroeconómicas por país (métodos: spline cúbico, ARIMA, auto-ARIMA, MICE).

## Modelo y entrenamiento

- **`G2Av1513.py`** — motor Glicko-2 modificado vigente. Implementa el ciclo completo de Glickman (conversión de escala, `g(φ)`, actualización de volatilidad con método de Illinois, actualización de rating) con tres mejoras sobre el motor canónico de Shelopugin-Sirotkin:
  1. Corrección algebraica del denominador trinomial (`1 + exp(Δ) + exp(d + s_logit)`), que garantiza que las probabilidades H/D/A sumen 1 por construcción y evita desbordamiento del simplex.
  2. Transformación logit interna del parámetro de empate: el motor recibe `P_D` en escala de probabilidad y aplica `s_logit = log(P_D / (1 − P_D))` internamente, mejorando la estabilidad numérica del optimizador.
  3. Constante de suavizado `g_constant = 3/π²` (en vez del `1.5/π²` canónico), derivada empíricamente durante la calibración.
  
  La ventaja de localía entra como combinación lineal de features externas vía `self.beta` (`_compute_advantage`). En producción se pasa una única feature: el parámetro `h` calibrado por liga.

- **`ModelTraining.ipynb`** — notebook único de entrenamiento. Consolida lo que originalmente fueron varios módulos de experimentación (deprecados a lo largo del desarrollo) e integra tres bloques internos:
  1. **Skellam/LGBM:** dos regresores LightGBM (Poisson) estiman goles esperados de local y visitante; la probabilidad de empate se deriva de la PMF de Skellam en `k=0`.
  2. **Glicko-2 calibrado:** optimización `L-BFGS-B` de `draw_adjustment` y del parámetro `h` por liga. Los parámetros calibrados se persisten en JSON y se releen dentro del mismo notebook al correr el Stage 2. Produce además los historiales y ratings finales por dataset.
  3. **Stage 2 (MLP):** red Keras que toma las probabilidades Glicko `(p_H, p_D, p_A)` — sola (config *Base*) o concatenada con las features exógenas (config *Completo*) — y se entrena en 10 corridas pareadas con semillas `Seed + run_id`, con test t pareado sobre Log-Loss categórico, Log-Loss binario y F1 de empates.

- **`Extra1.ipynb`** — baseline Naive Trinomial. Reimplementación autocontenida del motor Shelopugin-Sirotkin sin calibración (`draw_correction = 0.0`, `advantage = 0`), usada como referencia contra el motor calibrado. Genera también las validaciones contra rankings institucionales (UEFA, AFC, CONMEBOL, ELO) con Spearman ρ, Kendall τ, Overlap@10, Overlap@20 y MAE posicional.

- **`Extra2.ipynb`** — proyección ARIMA/LSTM. Panel semanal univariado de `μ` por equipo; ARIMA one-step recursivo y LSTM global con ventana de 8 semanas. Split cronológico 70/30 y proyección a 52 semanas.

### Selectores de ejecución en `ModelTraining`

- `TRAINING_MODE ∈ {sanity, fast, deep, exhaustive}` — controla épocas, iteraciones del optimizador y tamaño del modelo.
- `DATASET_MODE ∈ {lite, full, lite_recortado, full_lite, full_lite_recortado}` — controla qué datasets se procesan en una corrida.

## Código de referencia externo

Las carpetas **`Data/GlickoSoccer-master/`** y **`Extras/GlickoSoccerMaster/`** contienen (duplicado en dos ubicaciones) el repositorio original de **Andrei Shelopugin y Alexander Sirotkin**, ["Ratings of European and South American Football Leagues Based on Glicko-2 with Modifications"](https://arxiv.org/abs/2310.11459) (2023) — el modelo Glicko-2 modificado en el que se basa este TEG, con soporte para empates vía la función de Skellam y predicciones usando CatBoost/LightGBM. `Extras/GlickoSoccerMaster/` incluye además el PDF del paper original. Este código se conserva **sin modificar**, como referencia; no es código propio del proyecto.

```bibtex
@misc{shelopugin2023ratings,
      title={Ratings of European and South American Football Leagues Based on Glicko-2 with Modifications},
      author={Andrei Shelopugin and Alexander Sirotkin},
      year={2023},
      eprint={2310.11459},
      archivePrefix={arXiv},
      primaryClass={stat.AP}
}
```
