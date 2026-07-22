# glicko2-match-predictor

Trabajo Especial de Grado (TEG) — modelo de nivel de poder para ligas de fútbol que integra rendimiento deportivo, factores económicos y factores ambientales, sobre un motor Glicko-2 modificado (siguiendo como base el paper de Shelopugin & Sirotkin, 2023). Cobertura: 15 ligas nacionales, temporadas 2017-18 a 2024-25.

## Estructura del repositorio

```
glicko2-match-predictor/
├── G2Av1513.py                 # Motor Glicko-2 modificado vigente (clase Glicko2G2A)
├── ModelTraining.ipynb          # Entrenamiento/optimización de la ventaja de localía (β) — v1
├── ModelTrainingV2.ipynb        # Entrenamiento — v2 (incluye intento de RNN/GRU en PyTorch)
├── ModelTrainingV3.ipynb        # Entrenamiento — v3, orientado a Google Colab, modo "fast"
├── ModelTrainingV4.ipynb        # Entrenamiento — v4, orientado a Google Colab, modo "deep"
├── Data/
│   ├── CargaDatos.ipynb                  # Carga y consolidación inicial de datos crudos
│   ├── LimpiezaParte1.ipynb              # Consolidación de archivos scrapeados, filtrado de ligas y de fútbol femenino
│   ├── LimpiezaParte2.ipynb              # Merge de clima/economía, feature engineering (diff_*), fit de β
│   ├── MacroEconomyEst.ipynb             # Imputación de variables macroeconómicas por país
│   ├── ScraperV4c.py                     # Scraper de datos deportivos (fbref.com)
│   ├── EstadiumsDB.csv                   # Estadios: país, equipo, coordenadas, capacidad, altura, asistencia
│   ├── MacroEconomy2_imputado.csv        # Variables macroeconómicas por país/año, ya imputadas
│   ├── TeamsEconomyDB.csv                # Economía de clubes: plantilla, valor de mercado, gastos/ingresos por fichajes
│   ├── team_name_review.csv              # Tabla de normalización de nombres de equipos
│   ├── matches_definitive_complete.csv   # Dataset consolidado (36.267 filas)
│   ├── matches_definitive_model_full.csv # Dataset final, feature set completo (25.275 filas)
│   ├── matches_definitive_model_lite.csv # Dataset final, feature set reducido (36.241 filas)
│   ├── matches_final_model_full2.csv     # Variante final, feature set completo (25.095 filas)
│   ├── matches_final_model_clean2.csv    # Variante final, limpieza adicional (35.652 filas)
│   ├── g2av1416_betas.pkl                # Coeficientes β entrenados de la ventaja de localía (producción)
│   ├── g2av1416_scaler.pkl               # StandardScaler asociado a esas features
│   ├── GlickoSoccer-master/              # Repo de referencia externo (ver "Código de referencia")
│   └── Legacy Data/, Legacy ipynb py/    # Versiones anteriores de scrapers y del motor G2A
└── Extras/
    └── GlickoSoccerMaster/                # Segunda copia del repo de referencia + paper original en PDF
```

## Convención de nombres de versión

Los scripts del motor siguen el patrón **`G2Av{x}{m}{dd}`** / **`MasterG2Av{x}{m}{dd}`**:

- **`MasterG2Av{x}{m}{dd}.py`** — script orquestador: carga las métricas (deportivas, económicas, ambientales) y llama a las funciones del motor G2A para obtener probabilidades y métricas por partido.
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
| `team_match_concat_normalized.csv` | Métricas deportivas por partido y equipo: asistencias, tarjetas, centros, faltas (cometidas/recibidas), goles, intercepciones, autogoles, fuera de juego, penales (goles/intentos/concedidos/ganados), tiros (totales/al arco), duelos ganados, fecha, equipos local/visitante, competencia. Solo primera división de los países del estudio. | FBRef.com |

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

- **`CargaDatos.ipynb`** — carga y consolidación inicial de los datos crudos scrapeados.
- **`LimpiezaParte1.ipynb`** — consolida los archivos scrapeados, filtra por las 15 ligas nacionales objetivo y torneos continentales asociados, y depura partidos de fútbol femenino (por `team_id`, combinando listas de equipos conocidos y detección heurística por nombre).
- **`LimpiezaParte2.ipynb`** — integra variables climáticas (Open-Meteo, agregadas por equipo/mes/año), variables económicas de club (`TeamsEconomyDB`) y macro (`MacroEconomy2_imputado`), calcula las features de diferencia (`diff_*`) usadas por el modelo, y ajusta los coeficientes β de la ventaja de localía.
- **`MacroEconomyEst.ipynb`** — imputa los valores faltantes de las variables macroeconómicas por país (métodos: spline cúbico, ARIMA, auto-ARIMA, MICE).

## Modelo y entrenamiento

- **`G2Av1513.py`** — motor Glicko-2 modificado vigente. Implementa el ciclo completo de Glickman (conversión de escala, cálculo de `g(φ)`, `Δ`, actualización de volatilidad y rating) más una ventaja de localía extendida como combinación lineal de features externas (`self.beta`), y probabilidades de victoria/empate/derrota vía una fórmula de denominador compartido (garantiza que las tres probabilidades sumen 1 por construcción).
- **`ModelTraining.ipynb` / `ModelTrainingV2.ipynb` / `ModelTrainingV3.ipynb` / `ModelTrainingV4.ipynb`** — notebooks de entrenamiento y experimentación: ajuste de los coeficientes β vía `scipy.optimize` (L-BFGS-B), un modelo LightGBM (Poisson) para estimar goles esperados y de ahí la probabilidad de empate (vía distribución de Skellam), y evaluación por LogLoss sobre distintos splits temporales. V3 y V4 están orientados a ejecución en Google Colab con un selector de modo (`sanity` / `fast` / `deep` / `exhaustive`).
- **`Data/g2av1416_betas.pkl`** / **`Data/g2av1416_scaler.pkl`** — coeficientes β entrenados actualmente en producción y el `StandardScaler` correspondiente a sus features (valor de mercado, distancia de viaje, gasto neto, temperatura, presión parcial de oxígeno, xG/xA/DDI recientes, asistencia local, jugadores extranjeros).

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
