import os
import time
import random
from datetime import datetime, timedelta
from typing import List, Dict

import requests
from bs4 import BeautifulSoup
import pandas as pd

BASE_URL = "https://fbref.com/en/matches/"

# Valores por defecto (los que ya usabas)
DEFAULT_START_DATE = "2021-07-01"  # o 2021-08-10 según prefieras
DEFAULT_END_DATE = "2024-06-30"

# Ligas a analizar (puedes añadir/quitar)
LEAGUES = [
    "eng Premier League",
    "es La Liga Table",
    "it Serie A Table",
    "de Bundesliga Table",
    "fr Ligue 1 Table",
    "gr Super League Greece Table",
    "us Major League Soccer Table",
    "mx Liga MX Table",
    "ar Liga Profesional Argentina Table",
    "br Série A Table",
    "ve Venezuelan Primera División Table",
    "kr K League 1 Table",
    "sa Saudi Pro League Table",
    "jp J1 League Table",
    "ir Persian Gulf Pro League Table",
]

#HEADERS = {
    #"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    #"AppleWebKit/537.36 (KHTML, like Gecko) "
    #"Chrome/115.0.0.0 Safari/537.36",
#}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,es-ES;q=0.8,es;q=0.7",
    "Referer": "https://fbref.com/",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

# Sesión persistente para reutilizar cookies y conexiones
SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def daterange(start_date: str, end_date: str):
    """Genera fechas entre start_date y end_date (YYYY-MM-DD)."""
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    for n in range(int((end - start).days) + 1):
        yield (start + timedelta(n)).strftime("%Y-%m-%d")


def fetch_matches_for_date(date: str) -> List[Dict]:
    """Devuelve lista de partidos (metadatos) para una fecha dada."""
    url = BASE_URL + date
    max_retries = 3
    response = None
    for attempt in range(1, max_retries + 1):
        try:
            # Pausa corta antes de cada request
            time.sleep(2 + random.uniform(0, 3))
            response = SESSION.get(url, timeout=15)
            if response.status_code == 200:
                break
            print(f"Intento {attempt}: status {response.status_code} en {url}")
        except requests.RequestException as e:
            print(f"Intento {attempt}: error de conexión a {url}: {e}")
        if attempt < max_retries:
            print("Reintentando en 10 segundos...")
            time.sleep(10)
    if response is None or response.status_code != 200:
        print(f"No se pudo acceder a {url} tras {max_retries} intentos.")
        return []

    time.sleep(5 + random.uniform(0, 5))  # Pausa para evitar detección
    soup = BeautifulSoup(response.text, "html.parser")
    matches: List[Dict] = []

    for table in soup.find_all("table"):
        caption = table.find("caption")
        league_name = caption.text.strip() if caption else None
        if league_name not in LEAGUES:
            continue
        for row in table.find_all("tr"):
            if not row.find("td"):
                continue
            data: Dict[str, str] = {}
            for cell in row.find_all("td"):
                stat = cell.get("data-stat")
                if stat:
                    data[stat] = cell.text.strip()
            home_team = data.get("home_team")
            away_team = data.get("away_team")
            score = data.get("score")
            if score and '-' in score:
                home_score_str, away_score_str = score.split('-')
                home_score = int(home_score_str.strip()) if home_score_str.strip().isdigit() else None
                away_score = int(away_score_str.strip()) if away_score_str.strip().isdigit() else None
            else:
                home_score, away_score = None, None
            score_cell = row.find('td', {'data-stat': 'score'})
            match_link = score_cell.find('a') if score_cell else None
            match_id = match_link['href'].split('/')[-1] if match_link else None
            source_url = f"https://fbref.com{match_link['href']}" if match_link else url
            match = {
                'date': date,
                'league': league_name,
                'home_team': home_team,
                'away_team': away_team,
                'home_score': home_score,
                'away_score': away_score,
                'match_id': match_id,
                'source_url': source_url,
            }
            matches.append(match)
    return matches


def download_match_tables(match: Dict, base_path: str) -> str:
    """Descarga todas las tablas y las concatena en un único CSV por partido."""
    url = match['source_url']
    print(f"  Descargando tablas de: {url}")
    response = None
    for attempt, wait_time in enumerate([10, 20, 30], start=1):
        try:
            # Pausa corta antes de cada request
            time.sleep(2 + random.uniform(0, 3))
            response = SESSION.get(url, timeout=20)
            if response.status_code == 200:
                break
            print(f"    Intento {attempt}: status {response.status_code}")
        except requests.RequestException as e:
            print(f"    Intento {attempt}: error {e}")
        print(f"    Esperando {wait_time} segundos antes de reintentar...")
        time.sleep(wait_time)
    if response is None or response.status_code != 200:
        print("    No se pudo descargar la página del partido.")
        return ""
    soup = BeautifulSoup(response.text, 'html.parser')
    from io import StringIO
    dfs = []
    for idx, table in enumerate(soup.find_all('table')):
        try:
            df = pd.read_html(StringIO(str(table)))[0]
            df.insert(0, '_table_index', idx)
            dfs.append(df)
        except Exception as e:
            print(f"    Error leyendo tabla {idx}: {e}")
        # Pausa ligera entre tablas
        time.sleep(2 + random.uniform(0, 2))
    if not dfs:
        print("    No se pudieron leer tablas de este partido.")
        return ""
    df_all = pd.concat(dfs, axis=0, ignore_index=True)
    # Eliminar columnas duplicadas (mantener primera aparición)
    seen = set()
    cols_clean = []
    for col in df_all.columns:
        base = col if isinstance(col, str) else col[1] if isinstance(col, tuple) else str(col)
        if base in seen:
            continue
        seen.add(base)
        cols_clean.append(col)
    df_all = df_all.loc[:, cols_clean]
    date_str = match['date']
    try:
        fecha = datetime.strptime(date_str, '%Y-%m-%d').strftime('%d-%m-%Y')
    except Exception:
        fecha = date_str
    league_short = (match['league'] or '').split('Table')[0].strip().replace(' ', '_')
    home = (match['home_team'] or '').replace(' ', '_')
    away = (match['away_team'] or '').replace(' ', '_')
    filename = f"{league_short}-{fecha}-{home}-vs_{away}.csv"
    filepath = os.path.join(base_path, filename)
    df_all.to_csv(filepath, index=False)
    print(f"    Guardado CSV de partido: {filename}")
    time.sleep(5 + random.uniform(0, 5))
    return filepath


def main():
    print("=== ScrapperV2 FBref (tablas completas por partido) ===")

    start_input = input(
        f"Fecha de inicio (yyyy-mm-dd, 'predet' o ENTER = {DEFAULT_START_DATE}): "
    ).strip()
    if not start_input or start_input.lower() == "predet":
        start_date = DEFAULT_START_DATE
    else:
        start_date = start_input

    end_input = input(
        f"Fecha de fin   (yyyy-mm-dd, 'predet' o ENTER = {DEFAULT_END_DATE}): "
    ).strip()
    if not end_input or end_input.lower() == "predet":
        end_date = DEFAULT_END_DATE
    else:
        end_date = end_input

    base_path = os.path.dirname(os.path.abspath(__file__))
    print(f"Carpeta de salida: {base_path}")

    all_matches: List[Dict] = []

    for date in daterange(start_date, end_date):
        print(f"Procesando fecha: {date}")
        matches = fetch_matches_for_date(date)
        print(f"  Partidos encontrados: {len(matches)}")
        for match in matches:
            csv_path = download_match_tables(match, base_path)
            match["csv_path"] = csv_path
            # Pausa ligera entre partidos
            time.sleep(2 + random.uniform(0, 3))
        all_matches.extend(matches)

    if not all_matches:
        print("No se descargaron partidos en el rango indicado.")
        return

    df_meta = pd.DataFrame(all_matches)
    campos = [
        "date",
        "league",
        "home_team",
        "away_team",
        "home_score",
        "away_score",
        "match_id",
        "source_url",
        "csv_path",
    ]
    df_meta = df_meta[campos]

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    meta_file = os.path.join(base_path, f"partidos_meta_{ts}.csv")
    df_meta.to_csv(meta_file, index=False)
    print(f"Metadatos de partidos guardados en: {meta_file}")


if __name__ == "__main__":
    main()
