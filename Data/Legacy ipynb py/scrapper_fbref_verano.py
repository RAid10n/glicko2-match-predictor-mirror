import requests
from bs4 import BeautifulSoup
import pandas as pd
import time
import os
import random
from datetime import datetime, timedelta

# Configuración inicial
START_DATE = '2021-08-10'  # Fecha de inicio de la temporada 2021-2022
DEFAULT_START_DATE = START_DATE
END_DATE = '2024-06-30'    # Fecha final de la temporada 2023-2024
BASE_URL = 'https://fbref.com/en/matches/'

# Ligas a analizar (ajusta según lo que encuentres en FBref)
LEAGUES = [
    'eng Premier League',
    'es La Liga Table',
    'it Serie A Table',
    'de Bundesliga Table',
    'fr Ligue 1 Table',
    'gr Super League Greece Table',
    'us Major League Soccer Table',
    'mx Liga MX Table',
    'ar Liga Profesional Argentina Table',
    'br Série A Table',
    've Venezuelan Primera División Table',
    'kr K League 1 Table',
    'sa Saudi Pro League Table',
    'jp J1 League Table',
    'ir Persian Gulf Pro League Table'
    
]

def daterange(start_date, end_date):
    """Genera fechas entre start_date y end_date (YYYY-MM-DD)."""
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    for n in range(int((end - start).days) + 1):
        yield (start + timedelta(n)).strftime("%Y-%m-%d")

def save_team_tables(match, base_path):
    """Descarga y guarda las tablas de estadísticas de cada equipo para un partido."""
    url = match['source_url']
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36'}
    response = None
    success = False
    for i, wait_time in enumerate([10, 20, 30], 1):
        try:
            response = requests.get(url, headers=headers, timeout=15)
            if response.status_code == 200:
                success = True
                break
            else:
                print(f"Intento {i}: No se pudo acceder a {url} (status {response.status_code})")
        except requests.RequestException as e:
            print(f"Intento {i}: Error de conexión a {url}: {e}")
        print(f"Esperando {wait_time} segundos antes de reintentar...")
        time.sleep(wait_time)
    if not success:
        print(f"No se pudo acceder a {url} tras 3 intentos. Se registra en el log.")
        log_path = os.path.join(base_path, 'errores_descarga_partidos.log')
        with open(log_path, 'a', encoding='utf-8') as logf:
            logf.write(f"{url}\n")
        return
    soup = BeautifulSoup(response.text, 'html.parser')
    from io import StringIO
    tables = soup.find_all('table')
    N = len(tables)
    home_team = match['home_team'].replace(' ', '_')
    away_team = match['away_team'].replace(' ', '_')
    league = match['league'].split('Table')[0].strip().replace(' ', '_')
    date_str = match['date']
    try:
        fecha = datetime.strptime(date_str, "%Y-%m-%d").strftime("%d-%m-%Y")
    except:
        fecha = date_str

    # Nombres de archivo con rival y condición
    # Local
    home_filename = f"{home_team}-{league}-local-vs_{away_team}_{fecha}.csv"
    # Visitante
    away_filename = f"{away_team}-{league}-visitante-vs_{home_team}_{fecha}.csv"
    # Columnas típicas de tabla Shots
    shots_cols = [
        'Minute', 'Player', 'Squad', 'xG', 'PSxG', 'Outcome', 'Distance', 'Body Part', 'Notes',
        ('SCA 1', 'Player'), ('SCA 1', 'Event'), ('SCA 2', 'Player'), ('SCA 2', 'Event')
    ]
    # Palabras clave de Team Stats
    team_stats_keywords = ['Team Stats', 'Possession', 'Passing Accuracy', 'Shots on Target', 'Saves', 'Cards']

    def is_shots_table(df):
        cols = df.columns
        if hasattr(cols, 'levels') and len(cols.levels) == 2:
            flat_cols = [(str(a), str(b)) for a, b in zip(cols.levels[0], cols.levels[1])]
            return all(any(str(sc[0]) in str(fc[0]) and str(sc[1]) in str(fc[1]) for fc in flat_cols) for sc in shots_cols)
        else:
            return all(str(sc) in [str(c) for c in cols] for sc in shots_cols[:9])

    def is_team_stats_table(df):
        cols = [str(c) for c in df.columns]
        if any(kw in col for kw in team_stats_keywords for col in cols):
            return True
        for kw in team_stats_keywords:
            if df.astype(str).apply(lambda x: x.str.contains(kw)).any().any():
                return True
        return False

    # Filtrar tablas: excluir Shots y Team Stats
    filtered_tables = []
    for idx, table in enumerate(tables):
        try:
            df = pd.read_html(StringIO(str(table)))[0]
            if not is_shots_table(df) and not is_team_stats_table(df):
                filtered_tables.append((idx, table, df))
        except Exception as e:
            print(f"Error leyendo tabla {idx}: {e}")

    Nf = len(filtered_tables)
    if Nf < 2:
        print("No hay suficientes tablas para dividir entre equipos.")
        return

    # Tablas secundarias para cada equipo (ignorando 0 y 1)
    remaining = filtered_tables[2:]
    half = len(remaining) // 2
    home_tables = [t[2] for t in remaining[:half]]
    away_tables = [t[2] for t in remaining[half:]]

    def concat_and_clean(tables):
        # Concatenar todas las tablas
        df = pd.concat(tables, axis=0, ignore_index=True)
        # Eliminar filas 'Bench'
        df = df[df.iloc[:,0] != 'Bench'].copy()
        # Eliminar columnas de identificación duplicadas
        id_cols = ['Player', '#', 'Nation', 'Pos', 'Age', 'Min']
        # Mantener solo la primera aparición de cada columna de identificación
        seen = set()
        cols_clean = []
        for col in df.columns:
            col_base = col if isinstance(col, str) else col[1] if isinstance(col, tuple) else str(col)
            if col_base in id_cols:
                if col_base not in seen:
                    cols_clean.append(col)
                    seen.add(col_base)
            else:
                cols_clean.append(col)
        df = df.loc[:, cols_clean]
        # Eliminar duplicados por nombre de jugador
        df = df.drop_duplicates(subset=[df.columns[0]], keep='first')
        return df

    # Guardar CSVs con nueva nomenclatura
    if home_tables:
        df_home = concat_and_clean(home_tables)
        filepath = os.path.join(base_path, home_filename)
        df_home.to_csv(filepath, index=False)
        print(f"Guardado: {home_filename}")
        time.sleep(4)  # Espera tras guardar tabla de equipo local
    if away_tables:
        df_away = concat_and_clean(away_tables)
        filepath = os.path.join(base_path, away_filename)
        df_away.to_csv(filepath, index=False)
        print(f"Guardado: {away_filename}")
        time.sleep(4)  # Espera tras guardar tabla de equipo visitante
def fetch_matches_for_date(date):
    url = BASE_URL + date
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36'}
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            response = requests.get(url, headers=headers, timeout=15)
            if response.status_code == 200:
                break
            else:
                print(f"Intento {attempt}: No se pudo acceder a {url} (status {response.status_code})")
        except requests.RequestException as e:
            print(f"Intento {attempt}: Error de conexión a {url}: {e}")
        if attempt < max_retries:
            print("Reintentando en 10 segundos...")
            time.sleep(10)
    else:
        # Si fallan los 3 intentos, esperar 10 minutos y reintentar una vez más
        print(f"Fallo definitivo tras {max_retries} intentos. Esperando 10 minutos antes de reintentar {url}...")
        time.sleep(600)
        try:
            response = requests.get(url, headers=headers, timeout=15)
            if response.status_code == 200:
                print("¡Éxito tras esperar 10 minutos!")
            else:
                print(f"Intento extra tras 10 minutos falló (status {response.status_code}). Esperando 45 minutos antes de un último intento...")
                time.sleep(2700)
                response = requests.get(url, headers=headers, timeout=15)
                if response.status_code == 200:
                    print("¡Éxito tras esperar 45 minutos!")
                else:
                    print(f"Último intento tras 45 minutos falló (status {response.status_code}). El scraping se detiene en la fecha {date}.")
                    print(f"El scraping se detuvo en la fecha: {date}")
                    exit(1)
        except requests.RequestException as e:
            print(f"Error de conexión tras 10 o 45 minutos: {e}. El scraping se detiene en la fecha {date}.")
            print(f"El scraping se detuvo en la fecha: {date}")
            exit(1)
    time.sleep(30 + random.uniform(0, 10))  # Pausa de 30 a 40 segundos entre solicitudes (aleatoria para evitar detección)
    soup = BeautifulSoup(response.text, 'html.parser')
    matches = []
    for table in soup.find_all('table'):
        caption = table.find('caption')
        league_name = caption.text.strip() if caption else None
        if league_name not in LEAGUES:
            continue
        for row in table.find_all('tr'):
            if not row.find('td'):
                continue
            data = {}
            for cell in row.find_all('td'):
                stat = cell.get('data-stat')
                if stat:
                    data[stat] = cell.text.strip()
            home_team = data.get('home_team')
            away_team = data.get('away_team')
            score = data.get('score')
            if score and '-' in score:
                home_score, away_score = score.split('-')
                home_score = int(home_score.strip()) if home_score.strip().isdigit() else None
                away_score = int(away_score.strip()) if away_score.strip().isdigit() else None
            else:
                home_score, away_score = None, None
            match_link = row.find('td', {'data-stat': 'score'}).find('a') if row.find('td', {'data-stat': 'score'}) else None
            match_id = match_link['href'].split('/')[-1] if match_link else None
            source_url = f"https://fbref.com{match_link['href']}" if match_link else url
            attendance = data.get('attendance')
            stadium = data.get('venue')
            referee = data.get('referee')
            raw_html = str(row)
            match = {
                'date': date,
                'league': league_name,
                'home_team': home_team,
                'away_team': away_team,
                'home_score': home_score,
                'away_score': away_score,
                'match_id': match_id,
                'source_url': source_url,
                'attendance': attendance,
                'stadium': stadium,
                'referee': referee,
                'raw_html': raw_html
            }
            matches.append(match)
    return matches

if __name__ == "__main__":
    # Solicitar fecha de inicio al usuario
    user_input = input(f"Ingrese la fecha de inicio (formato yyyy-mm-dd) o escriba 'predet' para usar la fecha predeterminada ({DEFAULT_START_DATE}): ").strip()
    if user_input.lower() == 'predet' or user_input == '':
        START_DATE = DEFAULT_START_DATE
    else:
        START_DATE = user_input

    base_path = os.path.dirname(os.path.abspath(__file__))
    all_matches = []
    for date in daterange(START_DATE, END_DATE):
        print(f"Procesando fecha: {date}")
        matches = fetch_matches_for_date(date)
        print(f"Partidos extraídos para {date}: {len(matches)}")
        for partido in matches:
            save_team_tables(partido, base_path)
            time.sleep(10 + random.uniform(0, 5))  # Espera entre partidos para evitar baneo
        all_matches.extend(matches)
    # Mostrar solo los campos relevantes
    df = pd.DataFrame(all_matches)
    campos = ['date', 'league', 'home_team', 'away_team', 'home_score', 'away_score', 'match_id', 'source_url', 'attendance', 'stadium', 'referee']
    print(df[campos])
    # Guardar en CSV
    df[campos].to_csv(f'partidos_{START_DATE}_a_{END_DATE}.csv', index=False)
    print(f"Archivo CSV guardado como partidos_{START_DATE}_a_{END_DATE}.csv")