import os
import time
import random
import unicodedata
import re
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple

import requests
from bs4 import BeautifulSoup, Comment
import pandas as pd

from urllib3.util import Retry
from requests.adapters import HTTPAdapter


# === Configuración base ===
BASE_URL = "https://fbref.com/en/matches/"

# Rangos por defecto (ajústalos según tus temporadas a analizar)
DEFAULT_START_DATE = "2021-07-01"
DEFAULT_END_DATE = "2024-06-30"

# Ligas a filtrar por el caption de las tablas del índice diario de FBref
# Nota: FBref usa captions como "eng Premier League", "es La Liga Table" en el índice.
LEAGUES = [
    "en Premier League",
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


# === Rate limiting global (evitar bloqueos) ===
# Modo lento recomendado cuando hay 403 frecuentes.
SLOW_MODE = True
# Modo Selenium: 'prefer' → usa Selenium primero; 'fallback' → Selenium sólo si requests falla.
SELENIUM_MODE = 'prefer'  # valores: 'prefer' | 'fallback'
# Volcado de HTML cuando no se encuentran partidos (para depurar)
DUMP_HTML_ON_EMPTY = False
MIN_REQ_SPACING_RANGE = (12.0, 20.0) if SLOW_MODE else (5.0, 9.0)
_NEXT_ALLOWED_TIME = 0.0

# Logs de fallos y cola de reintentos
FAILED_URL_LOG: List[Dict] = []
FAILED_MATCH_LOG: List[Dict] = []
FAILED_MATCH_DOWNLOADS: List[Dict] = []  # {'kind': 'players'|'all', 'match': match}


def _now() -> float:
    return time.monotonic()


def _schedule_next(min_spacing: Optional[float] = None):
    global _NEXT_ALLOWED_TIME
    spacing = min_spacing if min_spacing is not None else random.uniform(*MIN_REQ_SPACING_RANGE)
    _NEXT_ALLOWED_TIME = _now() + spacing


def _sleep_until_allowed():
    wait = _NEXT_ALLOWED_TIME - _now()
    if wait > 0:
        time.sleep(wait)


def _log_failed_url(url: str, status: Optional[int] = None, reason: Optional[str] = None,
                    context: Optional[Dict] = None):
    try:
        FAILED_URL_LOG.append({
            'url': url,
            'status': status,
            'reason': reason,
            'ts': datetime.now().isoformat(timespec='seconds'),
            **(context or {})
        })
    except Exception:
        # No romper flujo por problemas de logging
        pass


def _accept_encoding() -> str:
    enc = ["gzip", "deflate"]
    try:
        import brotli  # type: ignore
        enc.append("br")
    except Exception:
        try:
            import brotlicffi  # type: ignore
            enc.append("br")
        except Exception:
            pass
    return ", ".join(enc)


# UA y lenguaje estables por sesión para evitar señales anómalas.
STABLE_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)
STABLE_LANG = "es-ES,es;q=0.9,en-US;q=0.8,en;q=0.7"


def _build_headers(referer: Optional[str] = None) -> Dict[str, str]:
    referer = referer or "https://fbref.com/"
    return {
        "User-Agent": STABLE_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": STABLE_LANG,
        "Accept-Encoding": _accept_encoding(),
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Referer": referer,
        # Cabeceras típicas de navegadores modernos
        "sec-ch-ua": '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-User": "?1",
    }


def _create_session() -> requests.Session:
    try:
        import cloudscraper  # type: ignore
        sess = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "mobile": False}
        )
        print("Usando cloudscraper para mitigar bloqueos (si Cloudflare está activo).")
    except Exception:
        sess = requests.Session()

    retry = Retry(
        total=5,
        connect=5,
        read=5,
        status=5,
        backoff_factor=1.5,
        status_forcelist=(500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "HEAD", "OPTIONS"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    sess.mount("https://", adapter)
    sess.mount("http://", adapter)
    return sess


SESSION = _create_session()


def _parse_retry_after(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    try:
        v = value.strip()
        if v.isdigit():
            return max(30.0, min(float(v), 600.0))
        # HTTP-date
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(v)
        delta = (dt - datetime.utcnow()).total_seconds()
        return max(30.0, min(delta, 900.0))
    except Exception:
        return None


def _request(url: str, referer: Optional[str] = None, timeout: int = 25) -> Optional[requests.Response]:
    _sleep_until_allowed()
    headers = _build_headers(referer)
    try:
        resp = SESSION.get(url, headers=headers, timeout=timeout, allow_redirects=True)
    except requests.RequestException as e:
        print(f"  Error de conexión a {url}: {e}")
        _log_failed_url(url, status=None, reason=f"exception: {type(e).__name__}", context={'stage': 'request'})
        _schedule_next(10 + random.uniform(0, 2))
        return None

    if resp.status_code == 200:
        _schedule_next()
        return resp
    elif resp.status_code in (403, 429):
        retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
        default_wait = (180 + random.uniform(0, 120)) if SLOW_MODE else (60 + random.uniform(0, 10))
        wait = retry_after if retry_after is not None else default_wait
        print(f"  Status {resp.status_code} (bloqueo). Esperando {int(wait)}s…")
        _log_failed_url(url, status=resp.status_code, reason='blocked', context={'stage': 'request'})
        _schedule_next(wait)
        return None
    elif 500 <= resp.status_code < 600:
        wait = 20 + random.uniform(0, 5)
        print(f"  Status {resp.status_code}. Reintento en {int(wait)}s…")
        _log_failed_url(url, status=resp.status_code, reason='server_error', context={'stage': 'request'})
        _schedule_next(wait)
        return None
    else:
        wait = 8 + random.uniform(0, 2)
        print(f"  Status {resp.status_code}. Espera corta {int(wait)}s…")
        _log_failed_url(url, status=resp.status_code, reason='http_error', context={'stage': 'request'})
        _schedule_next(wait)
        return None


# === Selenium (undetected-chromedriver) soporte ===
_SELENIUM_DRIVER = None


def _get_selenium_driver():
    global _SELENIUM_DRIVER
    if _SELENIUM_DRIVER is not None:
        return _SELENIUM_DRIVER
    try:
        import undetected_chromedriver as uc  # type: ignore
    except Exception as e:
        print(f"  Selenium no disponible: {e}")
        return None

    # Usar las ChromeOptions de undetected-chromedriver para mayor compatibilidad
    try:
        opts = uc.ChromeOptions()  # type: ignore
    except Exception:
        from selenium.webdriver.chrome.options import Options  # type: ignore
        opts = Options()  # fallback

    # Headless moderno + mitigaciones comunes
    opts.add_argument("--headless=new")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument(f"--user-agent={STABLE_UA}")
    opts.add_argument("--lang=es-ES")
    # Desactivar automatización evidente
    try:
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])  # type: ignore
        opts.add_experimental_option('useAutomationExtension', False)  # type: ignore
    except Exception:
        pass

    # Intentar detectar Chrome en Windows si no está en PATH y pasar por parámetro dedicado
    browser_path = None
    try:
        import os as _os
        candidates = [
            _os.environ.get("CHROME_BIN"),
            _os.path.join(_os.environ.get("PROGRAMFILES", ""), "Google", "Chrome", "Application", "chrome.exe"),
            _os.path.join(_os.environ.get("PROGRAMFILES(X86)", ""), "Google", "Chrome", "Application", "chrome.exe"),
            _os.path.join(_os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "Application", "chrome.exe"),
        ]
        candidates = [p for p in candidates if p]
        for p in candidates:
            if _os.path.exists(p):
                browser_path = str(p)
                break
    except Exception:
        browser_path = None

    try:
        # version_main ayuda a seleccionar el driver interno más compatible
        if browser_path:
            driver = uc.Chrome(options=opts, use_subprocess=True, browser_executable_path=browser_path, version_main=122)  # type: ignore
        else:
            driver = uc.Chrome(options=opts, use_subprocess=True, version_main=122)  # type: ignore
    except Exception as e:
        print(f"  No se pudo iniciar Chrome headless: {e}")
        # Fallback a Microsoft Edge (Selenium Manager descarga driver automáticamente)
        try:
            from selenium.webdriver import Edge  # type: ignore
            from selenium.webdriver.edge.options import Options as EdgeOptions  # type: ignore
            eopts = EdgeOptions()
            eopts.add_argument("--headless=new")
            eopts.add_argument("--disable-gpu")
            eopts.add_argument("--no-sandbox")
            eopts.add_argument("--disable-dev-shm-usage")
            eopts.add_argument("--disable-blink-features=AutomationControlled")
            eopts.add_argument(f"--user-agent={STABLE_UA}")
            eopts.add_argument("--lang=es-ES")
            driver = Edge(options=eopts)
        except Exception as ee:
            print(f"  Tampoco se pudo iniciar Edge headless: {ee}")
            return None

    # Cerrar automáticamente al salir
    try:
        import atexit
        atexit.register(lambda: (driver.quit() if driver else None))
    except Exception:
        pass

    _SELENIUM_DRIVER = driver
    return driver


def _selenium_get_html(url: str, referer: Optional[str]) -> Optional[str]:
    _sleep_until_allowed()
    driver = _get_selenium_driver()
    if driver is None:
        return None
    try:
        base_ref = referer or "https://fbref.com/"
        try:
            driver.get(base_ref)
        except Exception:
            pass
        time.sleep(1.0 + random.uniform(0, 1.0))

        # Navegar manteniendo referer
        try:
            driver.execute_script("window.location.assign(arguments[0]);", url)
        except Exception:
            driver.get(url)

        # Esperar carga completa + presencia de tablas
        from selenium.webdriver.support.ui import WebDriverWait  # type: ignore
        from selenium.webdriver.support import expected_conditions as EC  # type: ignore
        from selenium.webdriver.common.by import By  # type: ignore
        WebDriverWait(driver, 30).until(lambda d: d.execute_script("return document.readyState") == "complete")
        try:
            WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, 'table')))
        except Exception:
            # intentar un pequeño scroll para disparar cargas perezosas, luego reintentar
            try:
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(1.0)
            except Exception:
                pass
        html = driver.page_source
        _schedule_next()
        return html
    except Exception as e:
        print(f"  Selenium error al obtener {url}: {e}")
        _schedule_next(10 + random.uniform(0, 5))
        return None


def _get_html(url: str, referer: Optional[str]) -> Optional[str]:
    """Devuelve HTML usando requests/cloudscraper o Selenium según modo y disponibilidad."""
    if SELENIUM_MODE == 'prefer':
        html = _selenium_get_html(url, referer)
        if html:
            return html
        resp = _request(url, referer)
        return resp.text if resp is not None else None
    else:
        resp = _request(url, referer)
        if resp is not None and resp.status_code == 200:
            return resp.text
        # fallback a selenium
        return _selenium_get_html(url, referer)


def daterange(start_date: str, end_date: str):
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    for n in range(int((end - start).days) + 1):
        yield (start + timedelta(n)).strftime("%Y-%m-%d")


def _normalize_team(name: Optional[str]) -> Optional[str]:
    if not name:
        return name
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.category(c) == "Mn")  # quitar tildes
    s = s.replace("\xa0", " ")  # NBSP
    s = " ".join(s.split())  # espacios repetidos
    # No forzar case que rompa acrónimos; solo recortar
    return s.strip()


def fetch_matches_for_date(date: str) -> List[Dict]:
    """Extrae partidos desde el índice diario: columnas mínimas + raw_row."""
    url = BASE_URL + date
    # Precalentamiento de sesión para ayudar con desafíos de bot/CF
    try:
        _get_html("https://fbref.com/en/", referer="https://fbref.com/")
        time.sleep(1.0 + random.uniform(0, 1.0))
    except Exception:
        pass
    html = _get_html(url, referer="https://fbref.com/en/")
    if not html:
        print(f"  No se pudo acceder al índice del día {date}")
        _log_failed_url(url, status=None, reason='index_failed', context={'stage': 'index', 'date': date})
        return []

    time.sleep(2.0 + random.uniform(0, 2.0))
    soup = BeautifulSoup(html, "html.parser")
    out: List[Dict] = []

    for table in soup.find_all("table"):
        caption = table.find("caption")
        league_name = caption.text.strip() if caption else None
        # Filtrar por liga si hay caption; si no hay, comprobar estructura esperada
        if league_name is not None and league_name not in LEAGUES:
            continue
        has_cols = table.find("td", {"data-stat": "home_team"}) and table.find("td", {"data-stat": "away_team"})
        if not has_cols:
            continue

        for row in table.find_all("tr"):
            if not row.find("td"):
                continue
            data: Dict[str, str] = {}
            for cell in row.find_all("td"):
                stat = cell.get("data-stat")
                if stat:
                    data[stat] = cell.text.strip()

            home_team = _normalize_team(data.get("home_team"))
            away_team = _normalize_team(data.get("away_team"))

            # Parseo robusto de marcador (soporta '1–0', '1-1 AET', 'Preview', etc.)
            def _parse_score(txt: Optional[str]):
                if not txt:
                    return None, None
                s = txt.strip().replace("\u2013", "-").replace("\u2014", "-")
                m = re.match(r"^(\d+)\s*-\s*(\d+)$", s)
                if m:
                    return int(m.group(1)), int(m.group(2))
                nums = re.findall(r"\d+", s)
                if len(nums) >= 2:
                    return int(nums[0]), int(nums[1])
                return None, None

            score_text = data.get("score")
            home_score, away_score = _parse_score(score_text)

            score_cell = row.find("td", {"data-stat": "score"})
            match_link = score_cell.find("a") if score_cell else None
            match_href = match_link.get("href") if match_link else None
            match_id = match_href.split("/")[-1] if match_href else None
            source_url = f"https://fbref.com{match_href}" if match_href else url

            # Fallback: si no pudimos obtener marcador del texto en 'data', intentar del cell
            if (home_score is None or away_score is None) and score_cell:
                cell_text = score_cell.get_text(strip=True)
                hs, as_ = _parse_score(cell_text)
                home_score = hs if home_score is None else home_score
                away_score = as_ if away_score is None else away_score

            out.append({
                "date": date,
                "league": league_name,
                "home_team": home_team,
                "away_team": away_team,
                "home_score": home_score,
                "away_score": away_score,
                "match_id": match_id,
                "source_url": source_url,
                # opcionales (no suelen estar en el índice diario)
                "attendance": None,
                "stadium": None,
                "referee": None,
                "raw_row": str(row),
            })

    # Si no se encontraron partidos, y se pidió volcar HTML, guardar un archivo de depuración
    if not out and DUMP_HTML_ON_EMPTY:
        try:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            base_path = os.path.dirname(os.path.abspath(__file__))
            fname = os.path.join(base_path, f"debug_fbref_index_{date}_{ts}.html")
            with open(fname, 'w', encoding='utf-8') as f:
                f.write(html)
            print(f"    [debug] HTML del índice guardado en: {fname}")
        except Exception as e:
            print(f"    [debug] No se pudo guardar HTML: {e}")

    return out


# === Extracción de tablas de estadísticas por jugador en página de partido ===
def _friendly_table_name(tb) -> str:
    cap = tb.find('caption')
    if cap and cap.text.strip():
        return cap.text.strip()
    for attr in ('aria-label', 'data-title', 'data-label', 'summary'):
        val = tb.get(attr)
        if val:
            return str(val)
    classes = tb.get('class')
    if classes:
        return ' '.join(classes)
    return ''


def _team_and_kind_from_id(table_id: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if not table_id:
        return None, None
    if table_id.startswith('stats_'):
        rest = table_id[len('stats_'):]
        parts = rest.split('_')
        if len(parts) >= 2:
            team = parts[0].replace('-', ' ').replace('_', ' ').strip()
            kind = '_'.join(parts[1:])
            return team, kind
    return None, None


def _flatten_columns(df: pd.DataFrame) -> pd.DataFrame:
    if any(isinstance(c, tuple) for c in df.columns):
        def _flat(c):
            if isinstance(c, tuple):
                return ' '.join([str(x) for x in c if x is not None]).strip()
            return str(c)
        df.columns = [_flat(c) for c in df.columns]
    return df


def _to_ymd(date_val) -> str:
    """Convierte valores de fecha (str, datetime, pd.Timestamp) a 'YYYY-MM-DD'.
    Si no se puede, devuelve str(date_val).
    """
    try:
        import pandas as _pd  # usar pandas si disponible
        if isinstance(date_val, (_pd.Timestamp, datetime)):
            return _pd.to_datetime(date_val).strftime('%Y-%m-%d')
    except Exception:
        pass
    if isinstance(date_val, datetime):
        return date_val.strftime('%Y-%m-%d')
    s = str(date_val) if date_val is not None else ''
    try:
        return datetime.strptime(s, '%Y-%m-%d').strftime('%Y-%m-%d')
    except Exception:
        return s


def _base_filename_players(match: Dict) -> str:
    """Construye el nombre base esperado para archivos de tablas de jugador."""
    date_str = _to_ymd(match.get('date', ''))
    try:
        fecha = datetime.strptime(date_str, '%Y-%m-%d').strftime('%d-%m-%Y')
    except Exception:
        fecha = date_str
    league_short = (match.get('league') or '').split('Table')[0].strip().replace(' ', '_')
    home = (match.get('home_team') or '').replace(' ', '_')
    away = (match.get('away_team') or '').replace(' ', '_')
    return f"players_{league_short}-{fecha}-{home}-vs_{away}"


def _base_filename_all(match: Dict) -> str:
    """Construye el nombre base esperado para archivos de todas las tablas del partido."""
    date_str = _to_ymd(match.get('date', ''))
    try:
        fecha = datetime.strptime(date_str, '%Y-%m-%d').strftime('%d-%m-%Y')
    except Exception:
        fecha = date_str
    league_short = (match.get('league') or '').split('Table')[0].strip().replace(' ', '_')
    home = (match.get('home_team') or '').replace(' ', '_')
    away = (match.get('away_team') or '').replace(' ', '_')
    return f"all_{league_short}-{fecha}-{home}-vs_{away}"


def _existing_output_for_players(match: Dict, base_path: str, save_mode: str) -> Optional[str]:
    base_filename = _base_filename_players(match)
    if save_mode == 'excel':
        path = os.path.join(base_path, base_filename + '.xlsx')
        return path if os.path.exists(path) else None
    elif save_mode == 'csv':
        manifest = os.path.join(base_path, base_filename + '__manifest.csv')
        # Requiere que el manifiesto exista y que todos los archivos listados existan
        ok, existing, total = _is_manifest_complete(manifest, base_path)
        if ok:
            print(f"    Manifiesto completo detectado (players): {existing}/{total}")
            return manifest
        elif os.path.exists(manifest):
            print(f"    Manifiesto incompleto (players): {existing}/{total}. Se reintentará descarga.")
        return None
    return None


def _existing_output_for_all(match: Dict, base_path: str, save_mode: str) -> Optional[str]:
    base_filename = _base_filename_all(match)
    if save_mode == 'excel':
        path = os.path.join(base_path, base_filename + '.xlsx')
        return path if os.path.exists(path) else None
    elif save_mode == 'csv_unico':
        path = os.path.join(base_path, base_filename + '.csv')
        return path if os.path.exists(path) else None
    elif save_mode == 'csv_por_tabla':
        manifest = os.path.join(base_path, base_filename + '__manifest.csv')
        ok, existing, total = _is_manifest_complete(manifest, base_path)
        if ok:
            print(f"    Manifiesto completo detectado (all): {existing}/{total}")
            return manifest
        elif os.path.exists(manifest):
            print(f"    Manifiesto incompleto (all): {existing}/{total}. Se reintentará descarga.")
        return None
    return None


def _is_manifest_complete(manifest_path: str, base_path: str) -> Tuple[bool, int, int]:
    """Verifica que el manifiesto CSV exista y que todos los archivos listados existan.
    Devuelve (ok, existentes, total).
    """
    if not os.path.exists(manifest_path):
        return False, 0, 0
    try:
        df = pd.read_csv(manifest_path)
    except Exception:
        return False, 0, 0
    if 'file' not in df.columns:
        total = len(df)
        return False, 0, total
    total = len(df)
    existing = 0
    try:
        for f in df['file'].astype(str).tolist():
            p = os.path.join(base_path, f)
            if os.path.exists(p):
                existing += 1
    except Exception:
        # Si hay error al iterar, considerarlo incompleto
        pass
    return (existing == total and total > 0), existing, total


def download_player_tables(match: Dict, base_path: str, save_mode: str = 'excel') -> str:
    """Descarga tablas de estadísticas por jugador del partido y las guarda.
    save_mode: 'excel' → una hoja por tabla; 'csv' → un CSV por tabla + manifiesto.
    Devuelve la ruta principal escrita (archivo Excel o manifiesto CSV).
    """
    url = match.get('source_url')
    if not url:
        return ''
    daily_referer = BASE_URL + _to_ymd(match.get('date', ''))
    # Saltar si ya existe salida previa
    existing = _existing_output_for_players(match, base_path, save_mode)
    if existing:
        print(f"  [player-tables] Saltando (ya existe): {os.path.basename(existing)}")
        return existing
    print(f"  [player-tables] Descargando: {url} (modo='{save_mode}')")

    html = _get_html(url, referer=daily_referer)
    if not html:
        print("    No se pudo descargar la página del partido para tablas de jugador.")
        try:
            FAILED_MATCH_LOG.append({
                'kind': 'players',
                'match_id': match.get('match_id'),
                'date': match.get('date'),
                'league': match.get('league'),
                'home_team': match.get('home_team'),
                'away_team': match.get('away_team'),
                'source_url': url,
                'reason': 'request_failed',
                'ts': datetime.now().isoformat(timespec='seconds')
            })
            FAILED_MATCH_DOWNLOADS.append({'kind': 'players', 'match': match})
        except Exception:
            pass
        return ''

    soup = BeautifulSoup(html, 'html.parser')
    tables = list(soup.find_all('table'))
    visible_count = len(tables)

    # Tablas dentro de comentarios HTML (FBref aloja muchas aquí)
    comments = soup.find_all(string=lambda t: isinstance(t, Comment))
    comment_tables = []
    for c in comments:
        if '<table' in c:
            try:
                cs = BeautifulSoup(c, 'html.parser')
                comment_tables.extend(cs.find_all('table'))
            except Exception:
                pass
    tables.extend(comment_tables)

    # De-duplicar por id o por firma del HTML
    seen_ids = set()
    seen_hashes = set()
    unique_tables = []
    for t in tables:
        tid = t.get('id')
        if tid:
            if tid in seen_ids:
                continue
            seen_ids.add(tid)
            unique_tables.append(t)
        else:
            sig = hash(str(t))
            if sig in seen_hashes:
                continue
            seen_hashes.add(sig)
            unique_tables.append(t)

    print(f"    Detectadas: visibles={visible_count}, en_comentarios={len(comment_tables)}, únicas={len(unique_tables)}")

    # Filtrar sólo tablas de jugadores/porteros (ids que comienzan con 'stats_')
    player_tables = []
    for t in unique_tables:
        tid = t.get('id')
        if tid and tid.startswith('stats_'):
            player_tables.append(t)

    print(f"    Tablas de jugador: {len(player_tables)}")

    # Convertir a DataFrames y anotar metadatos
    dfs: List[pd.DataFrame] = []
    infos: List[Dict] = []
    from io import StringIO
    for idx, tb in enumerate(player_tables):
        tid = tb.get('id') or ''
        team, kind = _team_and_kind_from_id(tid)
        name = _friendly_table_name(tb)
        try:
            df = pd.read_html(StringIO(str(tb)))[0]
            df = _flatten_columns(df)
            df.insert(0, '_table_index', idx)
            df.insert(1, '_table_id', tid)
            df.insert(2, '_table_name', name)
            df.insert(3, 'match_id', match.get('match_id'))
            df.insert(4, 'date', match.get('date'))
            df.insert(5, 'league', match.get('league'))
            df.insert(6, 'team', team)
            df.insert(7, 'kind', kind)
            dfs.append(df)
            infos.append({
                'idx': idx,
                'id': tid,
                'team': team,
                'kind': kind,
                'name': name,
                'rows': df.shape[0],
                'cols': df.shape[1],
            })
        except Exception as e:
            print(f"    Error leyendo tabla {idx}: {e}")
        time.sleep(1.2 + random.uniform(0, 1.2))

    # Si no hubo tablas
    if not dfs:
        print("    No se encontraron tablas de jugador para este partido.")
        return ''

    # Seleccionar nombre base del archivo
    base_filename = _base_filename_players(match)

    if save_mode == 'excel':
        filename = base_filename + '.xlsx'
        filepath = os.path.join(base_path, filename)
        with pd.ExcelWriter(filepath, engine='xlsxwriter') as writer:
            for info in infos:
                idx = info['idx']
                sheet_name = info['team'] or info['kind'] or f"tabla_{idx}"
                # Sanear y limitar nombre de hoja
                safe = (sheet_name or f"tabla_{idx}").replace(':',' ').replace('/',' ').replace('\\',' ').replace('*',' ').replace('?',' ').replace('[',' ').replace(']',' ').strip()
                if not safe:
                    safe = f"tabla_{idx}"
                sheet_name = (safe[:28] + f"_{idx}") if len(safe) > 28 else safe
                dfs[idx].to_excel(writer, index=False, sheet_name=sheet_name)
        print(f"    Guardado Excel por partido: {filename}")
        time.sleep(4 + random.uniform(0, 3))
        return filepath
    elif save_mode == 'csv':
        manifest = []
        last_path = ''
        for info in infos:
            idx = info['idx']
            team = (info['team'] or 'team').replace(' ', '_')
            kind = (info['kind'] or 'kind').replace(' ', '_')
            per_name = f"{base_filename}__{idx:02d}_{team}_{kind}.csv"
            per_path = os.path.join(base_path, per_name)
            dfs[idx].to_csv(per_path, index=False)
            last_path = per_path
            manifest.append({
                'file': per_name,
                'idx': idx,
                'id': info['id'],
                'team': info['team'],
                'kind': info['kind'],
                'name': info['name'],
                'rows': info['rows'],
                'cols': info['cols'],
            })
        man_df = pd.DataFrame(manifest)
        man_name = base_filename + '__manifest.csv'
        man_path = os.path.join(base_path, man_name)
        man_df.to_csv(man_path, index=False)
        print(f"    Guardados CSVs por tabla + manifiesto: {man_name}")
        time.sleep(4 + random.uniform(0, 3))
        return man_path
    else:
        print(f"    Modo '{save_mode}' no reconocido. Omite guardado.")
        return ''


# === Extracción de TODAS las tablas de la página del partido (como V2/V1, robusto) ===
def download_all_match_tables(match: Dict, base_path: str, save_mode: str = 'excel') -> str:
    """Descarga todas las tablas del partido (incluidas las ocultas en comentarios).
    save_mode: 'excel' → una hoja por tabla; 'csv_unico' → un solo CSV concatenado;
               'csv_por_tabla' → CSV por tabla + manifiesto.
    Devuelve la ruta principal escrita.
    """
    url = match.get('source_url')
    if not url:
        return ''
    daily_referer = BASE_URL + _to_ymd(match.get('date', ''))
    # Saltar si ya existe salida previa
    existing = _existing_output_for_all(match, base_path, save_mode)
    if existing:
        print(f"  [all-tables] Saltando (ya existe): {os.path.basename(existing)}")
        return existing
    print(f"  [all-tables] Descargando: {url} (modo='{save_mode}')")

    html = _get_html(url, referer=daily_referer)
    if not html:
        print("    No se pudo descargar la página del partido.")
        try:
            FAILED_MATCH_LOG.append({
                'kind': 'all',
                'match_id': match.get('match_id'),
                'date': match.get('date'),
                'league': match.get('league'),
                'home_team': match.get('home_team'),
                'away_team': match.get('away_team'),
                'source_url': url,
                'reason': 'request_failed',
                'ts': datetime.now().isoformat(timespec='seconds')
            })
            FAILED_MATCH_DOWNLOADS.append({'kind': 'all', 'match': match})
        except Exception:
            pass
        return ''

    soup = BeautifulSoup(html, 'html.parser')
    tables = list(soup.find_all('table'))
    visible_count = len(tables)

    comments = soup.find_all(string=lambda t: isinstance(t, Comment))
    comment_tables = []
    for c in comments:
        if '<table' in c:
            try:
                cs = BeautifulSoup(c, 'html.parser')
                comment_tables.extend(cs.find_all('table'))
            except Exception:
                pass
    tables.extend(comment_tables)

    seen_ids = set()
    seen_hashes = set()
    unique_tables = []
    for t in tables:
        tid = t.get('id')
        if tid:
            if tid in seen_ids:
                continue
            seen_ids.add(tid)
            unique_tables.append(t)
        else:
            sig = hash(str(t))
            if sig in seen_hashes:
                continue
            seen_hashes.add(sig)
            unique_tables.append(t)

    print(f"    Detectadas: visibles={visible_count}, en_comentarios={len(comment_tables)}, únicas={len(unique_tables)}")

    from io import StringIO
    dfs: List[pd.DataFrame] = []
    infos: List[Dict] = []

    def _tab_name(tb):
        nm = _friendly_table_name(tb)
        return nm if nm else (tb.get('id') or '')

    for idx, tb in enumerate(unique_tables):
        tid = tb.get('id') or ''
        name = _tab_name(tb)
        try:
            df = pd.read_html(StringIO(str(tb)))[0]
            df = _flatten_columns(df)
            df.insert(0, '_table_index', idx)
            df.insert(1, '_table_id', tid)
            df.insert(2, '_table_name', name)
            df.insert(3, 'match_id', match.get('match_id'))
            df.insert(4, 'date', match.get('date'))
            df.insert(5, 'league', match.get('league'))
            dfs.append(df)
            infos.append({
                'idx': idx,
                'id': tid,
                'name': name,
                'rows': df.shape[0],
                'cols': df.shape[1],
            })
        except Exception as e:
            print(f"    Error leyendo tabla {idx}: {e}")
        time.sleep(1.5 + random.uniform(0, 1.5))

    if not dfs:
        print("    No se pudieron leer tablas de este partido.")
        return ''

    base_filename = _base_filename_all(match)

    if save_mode == 'excel':
        filename = base_filename + '.xlsx'
        filepath = os.path.join(base_path, filename)
        with pd.ExcelWriter(filepath, engine='xlsxwriter') as writer:
            for info in infos:
                idx = info['idx']
                raw_name = info['name'] or f"tabla_{idx}"
                safe = raw_name.replace(':',' ').replace('/',' ').replace('\\',' ').replace('*',' ').replace('?',' ').replace('[',' ').replace(']',' ').strip()
                if not safe:
                    safe = f"tabla_{idx}"
                sheet_name = (safe[:28] + f"_{idx}") if len(safe) > 28 else safe
                dfs[idx].to_excel(writer, index=False, sheet_name=sheet_name)
        print(f"    Guardado Excel (todas las tablas): {filename}")
        time.sleep(4 + random.uniform(0, 3))
        return filepath
    elif save_mode == 'csv_unico':
        df_all = pd.concat(dfs, axis=0, ignore_index=True)
        filename = base_filename + '.csv'
        filepath = os.path.join(base_path, filename)
        df_all.to_csv(filepath, index=False)
        print(f"    Guardado CSV único (todas las tablas): {filename}")
        time.sleep(4 + random.uniform(0, 3))
        return filepath
    elif save_mode == 'csv_por_tabla':
        manifest = []
        for info in infos:
            idx = info['idx']
            raw_name = info['name'] or f"tabla_{idx}"
            safe = raw_name.replace(' ', '_').replace('/','_').replace('\\','_')
            per_name = f"{base_filename}__{idx:02d}_{safe}.csv"
            per_path = os.path.join(base_path, per_name)
            dfs[idx].to_csv(per_path, index=False)
            manifest.append({
                'file': per_name,
                'idx': idx,
                'id': info['id'],
                'name': info['name'],
                'rows': info['rows'],
                'cols': info['cols'],
            })
        man_df = pd.DataFrame(manifest)
        man_name = base_filename + '__manifest.csv'
        man_path = os.path.join(base_path, man_name)
        man_df.to_csv(man_path, index=False)
        print(f"    Guardados CSVs por tabla + manifiesto: {man_name}")
        time.sleep(4 + random.uniform(0, 3))
        return man_path
    else:
        print(f"    Modo '{save_mode}' no reconocido. Omite guardado.")
        return ''


def build_master_dataset(matches: List[Dict]) -> pd.DataFrame:
    df = pd.DataFrame(matches)
    if df.empty:
        return df

    # Tipado y orden
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df.sort_values(["date", "league"], inplace=True)

    # Tipos numéricos
    for col in ("home_score", "away_score"):
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")

    # Normalización adicional de nombres de equipos (por seguridad)
    df["home_team"] = df["home_team"].apply(_normalize_team)
    df["away_team"] = df["away_team"].apply(_normalize_team)

    # Duplicados: preferimos "match_id" si está; si no, la tupla (date, home_team, away_team)
    if "match_id" in df.columns:
        before = len(df)
        df = df.sort_values(["date"]).drop_duplicates(subset=["match_id"], keep="first")
        dup1 = before - len(df)
    else:
        dup1 = 0

    before2 = len(df)
    df = df.sort_values(["date"]).drop_duplicates(subset=["date", "home_team", "away_team"], keep="first")
    dup2 = before2 - len(df)

    print(f"  Duplicados eliminados por match_id: {dup1}")
    print(f"  Duplicados eliminados por (date, home, away): {dup2}")
    return df


def retry_failed_match_downloads(base_path: str, player_mode: str, all_mode: str) -> None:
    """Reintenta una vez las descargas de partidos que fallaron (players/all)."""
    if not FAILED_MATCH_DOWNLOADS:
        return
    print("\n=== Reintentando descargas fallidas de partidos ===")
    # Usar una copia para no modificar mientras iteramos
    pending = list(FAILED_MATCH_DOWNLOADS)
    # Limpiar la cola antes de reintentar
    FAILED_MATCH_DOWNLOADS.clear()
    for item in pending:
        kind = item.get('kind')
        match = item.get('match', {})
        # backoff ligero
        time.sleep(10 + random.uniform(0, 5))
        try:
            if kind == 'players':
                path = download_player_tables(match, base_path, save_mode=player_mode)
                if path:
                    print(f"  Reintento OK (players) para {match.get('match_id')} → {os.path.basename(path)}")
                else:
                    print(f"  Reintento falló (players) para {match.get('match_id')}")
            elif kind == 'all':
                path = download_all_match_tables(match, base_path, save_mode=all_mode)
                if path:
                    print(f"  Reintento OK (all) para {match.get('match_id')} → {os.path.basename(path)}")
                else:
                    print(f"  Reintento falló (all) para {match.get('match_id')}")
        except Exception as e:
            print(f"  Error en reintento para {match.get('match_id')}: {e}")
            try:
                FAILED_MATCH_LOG.append({
                    'kind': kind,
                    'match_id': match.get('match_id'),
                    'date': match.get('date'),
                    'league': match.get('league'),
                    'home_team': match.get('home_team'),
                    'away_team': match.get('away_team'),
                    'source_url': match.get('source_url'),
                    'reason': f'retry_exception: {type(e).__name__}',
                    'ts': datetime.now().isoformat(timespec='seconds')
                })
            except Exception:
                pass


def export_failure_logs(base_path: str) -> Tuple[Optional[str], Optional[str]]:
    """Exporta CSVs de fallos: URLs y partidos con problemas. Devuelve rutas."""
    url_path = match_path = None
    try:
        if FAILED_URL_LOG:
            df_urls = pd.DataFrame(FAILED_URL_LOG)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            url_path = os.path.join(base_path, f"failed_urls_{ts}.csv")
            df_urls.to_csv(url_path, index=False)
            print(f"Registro de URLs fallidas guardado: {url_path}")
        if FAILED_MATCH_LOG:
            df_matches = pd.DataFrame(FAILED_MATCH_LOG)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            match_path = os.path.join(base_path, f"failed_matches_{ts}.csv")
            df_matches.to_csv(match_path, index=False)
            print(f"Registro de partidos con problemas guardado: {match_path}")
    except Exception as e:
        print(f"No se pudo exportar logs de fallos: {e}")
    return url_path, match_path


def validate_and_report(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        print("No hay datos para validar.")
        return df

    # Reglas básicas de validación
    null_date = df["date"].isna().sum()
    null_league = df["league"].isna().sum()
    print(f"  Validación: date nulos={null_date}, league nulos={null_league}")

    missing_scores = df["home_score"].isna().sum() + df["away_score"].isna().sum()
    print(f"  Validación: puntuaciones faltantes={missing_scores}")

    # Conteos por liga
    counts = df.groupby("league").size().reset_index(name="count")
    print("  Conteos por liga:")
    for _, row in counts.iterrows():
        print(f"    {row['league']}: {row['count']}")

    # Referencia esperada: ~380 por temporada por liga; para dos temporadas: ~760
    # Fórmula: $20 \times 38 / 2 = 380$, dos temporadas: $2 \times 380 = 760$.
    # (Se usa como guía, no como restricción dura.)
    return counts


def main():
    print("=== ScraperV4 FBref (índice diario → dataset maestro) ===")

    start_input = input(
        f"Fecha de inicio (yyyy-mm-dd, ENTER = {DEFAULT_START_DATE}): "
    ).strip()
    start_date = start_input or DEFAULT_START_DATE

    end_input = input(
        f"Fecha de fin   (yyyy-mm-dd, ENTER = {DEFAULT_END_DATE}): "
    ).strip()
    end_date = end_input or DEFAULT_END_DATE

    # ÚNICA decisión: qué descargar.
    # Opciones: 1=maestro, 2=maestro+jugador, 3=maestro+todas, 4=todo, 5=solo_jugador, 6=solo_todas
    mode_in = input(
        "Selecciona qué descargar: [1] maestro, [2] maestro+jugador, [3] maestro+todas, [4] todo, [5] solo_jugador, [6] solo_todas (ENTER=3): "
    ).strip().lower() or '3'

    run_master = run_players = run_all = False
    if mode_in in ('1', 'maestro'):
        run_master = True
    elif mode_in in ('2', 'maestro+jugador', 'maestro+player'):
        run_master = True; run_players = True
    elif mode_in in ('3', 'maestro+todas', 'maestro+all'):
        run_master = True; run_all = True
    elif mode_in in ('4', 'todo', 'all'):
        run_master = True; run_players = True; run_all = True
    elif mode_in in ('5', 'solo_jugador', 'player_only'):
        run_players = True
    elif mode_in in ('6', 'solo_todas', 'all_only'):
        run_all = True
    else:
        print("Modo no reconocido, usando maestro por defecto.")
        run_master = True

    # Formatos por defecto para cada tipo (CSV por defecto en todo)
    master_out_fmt = 'csv'            # maestro: CSV por defecto
    player_mode = 'csv'               # tablas de jugador: CSV por tabla + manifiesto
    all_mode = 'csv_por_tabla'        # todas las tablas: CSV por tabla + manifiesto

    base_path = os.path.dirname(os.path.abspath(__file__))
    print(f"Carpeta de salida: {base_path}")

    all_matches: List[Dict] = []

    for date in daterange(start_date, end_date):
        print(f"Procesando fecha: {date}")
        day_matches = fetch_matches_for_date(date)
        print(f"  Partidos encontrados: {len(day_matches)}")
        all_matches.extend(day_matches)
        # pausa humana entre días (más lenta en SLOW_MODE)
        if SLOW_MODE:
            time.sleep(12.0 + random.uniform(0, 8.0))
        else:
            time.sleep(2.0 + random.uniform(0, 2.0))

    if not all_matches:
        print("No se encontraron partidos en el rango indicado.")
        return

    df_master = build_master_dataset(all_matches)

    # Validaciones rápidas y reporte en consola
    validate_counts = validate_and_report(df_master)

    # Guardado del dataset maestro (si corresponde)
    if run_master:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_file = os.path.join(base_path, f"matches_master_{ts}.{ 'xlsx' if master_out_fmt=='excel' else 'csv' }")
        if master_out_fmt == "excel":
            with pd.ExcelWriter(out_file, engine="xlsxwriter") as writer:
                df_master.to_excel(writer, index=False, sheet_name="matches")
                # hoja con conteos por liga
                validate_counts.to_excel(writer, index=False, sheet_name="league_counts")
            print(f"Dataset maestro guardado en Excel: {out_file}")
        else:
            df_master.to_csv(out_file, index=False)
            print(f"Dataset maestro guardado en CSV: {out_file}")

    # Extracción opcional de tablas de jugadores por partido
    if run_players:
        print("\n=== Extrayendo tablas de jugador por partido ===")
        for m in df_master.to_dict(orient='records'):
            path = download_player_tables(m, base_path, save_mode=player_mode)
            m['_player_tables_path'] = path
            time.sleep(6 + random.uniform(0, 4))

    # Extracción opcional de todas las tablas por partido
    if run_all:
        print("\n=== Extrayendo TODAS las tablas por partido ===")
        for m in df_master.to_dict(orient='records'):
            path = download_all_match_tables(m, base_path, save_mode=all_mode)
            m['_all_tables_path'] = path
            time.sleep(6 + random.uniform(0, 4))

    # Reintentos de descargas fallidas (si corresponde)
    retry_failed_match_downloads(base_path, player_mode=player_mode, all_mode=all_mode)

    # Exportar registros de fallos (URLs y partidos)
    export_failure_logs(base_path)

    # Sugerencias post-proceso (documentación básica)
    print("\nSugerencias de revisión:")
    print("- Verifica duplicados restantes y nombres de equipos.")
    print("- Revisa conteos por liga vs esperado (~760 por dos temporadas).")
    print("- Documenta partidos aplazados (scores faltantes) si aparecen.")


if __name__ == "__main__":
    main()