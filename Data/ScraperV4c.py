import os
import time
import random
import unicodedata
import re
import subprocess
from collections import deque
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple

import requests
from bs4 import BeautifulSoup, Comment
import pandas as pd

from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.edge.service import Service as EdgeService
from webdriver_manager.chrome import ChromeDriverManager
from webdriver_manager.microsoft import EdgeChromiumDriverManager


# Configuración base del selenium usando Edge como motor
BASE_URL = "https://fbref.com/en/matches/"
BASE_PATH = os.path.dirname(os.path.abspath(__file__))
COOKIES_FILE = os.path.join(BASE_PATH, "fbref_cookies.json")
PROFILE_DIR = os.path.join(BASE_PATH, ".selenium_profile")
EDGE_PROFILE_BASE = os.path.join(BASE_PATH, ".edge_profiles")
EDGE_DRIVER_LOG = os.path.join(BASE_PATH, "edgedriver.log")

# Rangos por defecto (ajústalos según tus temporadas a analizar
# Este era el rango inicial de estudio
# Actualmente es la opcion predeterminada
DEFAULT_START_DATE = "2021-07-01"
DEFAULT_END_DATE = "2024-06-30"

# Torneos a filtrar por coincidencia en el caption del índice diario de FBref
# Se comparan por coincidencia parcial (normalizada y sin tildes).
LEAGUE_PATTERNS = [
    "champions",
    "europa",
    "conference",
    "libertadores",
    "sudamericana",
    "premier",    
    "la liga",
    "serie a",
    "bundesliga",
    "ligue 1",
    "super league greece",
    "major league soccer",
    "liga mx",
    "liga profesional argentina",
    "série a",
    "venezuelan",
    "k league 1",
    "saudi pro",
    "j1 league",
    "persian gulf",
    "superliga"
]

# Patrones a excluir (si aparecen en el nombre de la liga, se descarta)
# Ejemplo: "ukrainian premier" para excluir la Premier de Ucrania.
LEAGUE_EXCLUDE_PATTERNS = [
    "belgian",
    "ukrainian premier",
    "russian premier",
    "canadian premier",
    "premiership",
    "ireland",
    "championship",
    "premier league 2",
    "copa",
    "austrian",
    "a1",
    "2",
    "u17",
    "u19",
    "frauen",
    "danish",
    "première",
    "serbian",
    "ecuadorian"
]

# Patrón de entrada para reintentar desde CSVs de URLs fallidas
# Ejemplo válido: failed_url_20260306_153045.csv
FAILED_URL_CSV_REGEX = re.compile(r"^failed_urls_(\d{8})_(\d{6})\.csv$", re.IGNORECASE)


# Rate limiting global para evitar bloqueos (error 403)
def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except Exception:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except Exception:
        return default


MIN_REQ_SPACING_RANGE = (
    _env_float("FBREF_MIN_SPACING_SECONDS", 6.0),
    _env_float("FBREF_MAX_SPACING_SECONDS", 10.0),
)
if MIN_REQ_SPACING_RANGE[0] > MIN_REQ_SPACING_RANGE[1]:
    MIN_REQ_SPACING_RANGE = (MIN_REQ_SPACING_RANGE[1], MIN_REQ_SPACING_RANGE[0])

RATE_WINDOW_SECONDS = max(60, _env_int("FBREF_RATE_WINDOW_SECONDS", 300))
RATE_MAX_REQUESTS = max(10, _env_int("FBREF_RATE_MAX_REQUESTS", 35))

# Pausas no relacionadas a red (parse/escritura) para no ralentizar innecesariamente.
LOCAL_PARSE_SLEEP_RANGE = (0.05, 0.25)
INTER_MATCH_SLEEP_RANGE = (0.25, 0.80)
INTER_DAY_SLEEP_RANGE = (1.5, 4.0)

_REQUEST_TIMES = deque()
_NEXT_ALLOWED_TIME = 1.0
_BLOCK_THRESHOLD = 3
CONSEC_BLOCKS = 0

# Logs de fallos y cola de reintentos
FAILED_URL_LOG: List[Dict] = []
FAILED_MATCH_LOG: List[Dict] = []
FAILED_MATCH_DOWNLOADS: List[Dict] = []  # {'kind': 'players'|'all', 'match': match}

# funcion que devuelve el tiempo
def _now() -> float:
    return time.monotonic()

# Funcion para calcular el siguiente intento
def _schedule_next(min_spacing: Optional[float] = None):
    global _NEXT_ALLOWED_TIME
    spacing = min_spacing if min_spacing is not None else random.uniform(*MIN_REQ_SPACING_RANGE)
    _NEXT_ALLOWED_TIME = _now() + spacing

# Funcion para esperar un tiempo determinado
def _sleep_until_allowed():
    wait = _NEXT_ALLOWED_TIME - _now()
    if wait > 0:
        time.sleep(wait)


def _soft_pause(pause_range: Tuple[float, float]) -> None:
    low, high = pause_range
    if low < 0:
        low = 0.0
    if high < low:
        high = low
    time.sleep(random.uniform(low, high))


def _enforce_window_limit() -> None:
    now = _now()
    while _REQUEST_TIMES and (now - _REQUEST_TIMES[0] > RATE_WINDOW_SECONDS):
        _REQUEST_TIMES.popleft()

    if len(_REQUEST_TIMES) >= RATE_MAX_REQUESTS:
        oldest = _REQUEST_TIMES[0]
        wait = (RATE_WINDOW_SECONDS - (now - oldest)) + random.uniform(0.2, 1.0)
        if wait > 0:
            print(
                f"  Rate-window activo ({len(_REQUEST_TIMES)}/{RATE_MAX_REQUESTS} en {RATE_WINDOW_SECONDS}s). "
                f"Pausa {wait:.1f}s…"
            )
            time.sleep(wait)
        now = _now()
        while _REQUEST_TIMES and (now - _REQUEST_TIMES[0] > RATE_WINDOW_SECONDS):
            _REQUEST_TIMES.popleft()

    _REQUEST_TIMES.append(_now())

# Funcion que guarda el link y tiempo de una descarga fallida 
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

# Header para hacer funcionar a Selenium
STABLE_UA = os.environ.get(
    "MY_UA",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36",
)
STABLE_LANG = "es-ES,es;q=0.9,en-US;q=0.8,en;q=0.7"
# Importante, pues de aumentar este tamano va a redescargar los manifest en bucle hasta que se cancele el script manualmente
MIN_VALID_FILE_BYTES = 1
FORCE_DAILY_WARMUP = os.environ.get("FBREF_DAILY_WARMUP", "0") == "1"
# Tiempo máximo para resolver el reto manualmente en warm-up (segundos)
WARMUP_TIMEOUT_SECONDS = int(os.environ.get("FBREF_WARMUP_TIMEOUT", "300"))

# Construimos los headers para usar Selenium
def _build_headers(referer: Optional[str] = None) -> Dict[str, str]:
    referer = referer or "https://fbref.com/"
    return {
        "User-Agent": STABLE_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": STABLE_LANG,
        "Accept-Encoding": "gzip, deflate, br",
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

# Funcion para usar un Chrome automatizado para saltar las barreras de Cloudflare
# Actualmente es Legacy, el codigo esta por si en algun momento se necesita
# Mas actualmente se usa otra funcion
def _create_session():
    """Crea una sesión HTTP con fingerprint TLS realista usando curl_cffi.
    Fallback a cloudscraper/requests si no está disponible.
    """
    try:
        from curl_cffi import requests as curl_requests  # type: ignore
        # Impersonar Chrome para handshake TLS idéntico
        sess = curl_requests.Session(impersonate="chrome")
        print("Usando curl_cffi con impersonate=chrome para mitigar fingerprinting TLS.")
        try:
            _load_session_cookies(sess)
        except Exception:
            pass
        try:
            _inject_manual_cf_clearance(sess)
        except Exception:
            pass
        return sess
    except Exception as e:
        print(f"  curl_cffi no disponible ({e}). Probando cloudscraper…")
        try:
            import cloudscraper  # type: ignore
            sess = cloudscraper.create_scraper(
                browser={"browser": "chrome", "platform": "windows", "mobile": False}
            )
            print("Usando cloudscraper como fallback.")
            return sess
        except Exception:
            print("  cloudscraper no disponible. Usando requests estándar.")
            return requests.Session()


SESSION = _create_session()
_WARMUP_DONE = False

# Funcion para detectar captchas
# Si detecta un captcha crea una sesion para resolverlos y obtener cookies
def _is_challenge_response(html: str) -> bool:
    """Detecta páginas de desafío (Cloudflare/DataDome) en el HTML."""
    markers = [
        "challenge-platform",  # Cloudflare challenge
        "cf-turnstile",        # Turnstile widget
        "__cf_chl_",           # Cloudflare params
        "Verifique que usted es un ser humano",  # texto común
        "Un momento…",          # título Cloudflare
    ]
    h = html or ""
    h_low = h.lower()
    return any(m.lower() in h_low for m in markers)

# Para obtener el chrome driver desde el exe del navegador
# Actualmente en desuso ya que el script usa Edge
def _find_chrome_exe() -> Optional[str]:
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
                return str(p)
    except Exception:
        pass
    return None

# Lo mismo de arriba pero para Edge, pero para buscar el edgedriver
# Actualmente en desuso ya que se usa la funcion para llamar a Edge
def _find_edgedriver_exe() -> Optional[str]:
    try:
        import os as _os
        import shutil as _shutil
        candidates = [
            _os.environ.get("EDGE_DRIVER_PATH"),
            _os.environ.get("MSEDGEDRIVER_PATH"),
            _os.environ.get("EDGE_DRIVER"),
            _os.path.join(BASE_PATH, "msedgedriver.exe"),
            _shutil.which("msedgedriver"),
            _shutil.which("edgedriver"),
        ]
        candidates = [p for p in candidates if p]
        for p in candidates:
            if _os.path.exists(p):
                return str(p)
    except Exception:
        pass
    return None

# Buscamos Edge para usarlo en sesion y obtener las cookies
def _find_edge_exe() -> Optional[str]:
    try:
        import os as _os
        candidates = [
            _os.environ.get("EDGE_BIN"),
            _os.path.join(_os.environ.get("PROGRAMFILES", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
            _os.path.join(_os.environ.get("PROGRAMFILES(X86)", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
            _os.path.join(_os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
        ]
        candidates = [p for p in candidates if p]
        for p in candidates:
            if _os.path.exists(p):
                return str(p)
    except Exception:
        pass
    return None

# Crea un perfil limpio y/o limpia el anterior usado para automatizar
# Esto minimiza errores en el llenado automatico del Captcha
def _get_edge_profile_dir(use_temp: bool) -> str:
    if not use_temp:
        return PROFILE_DIR
    try:
        os.makedirs(EDGE_PROFILE_BASE, exist_ok=True)
    except Exception:
        pass
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = str(random.randint(1000, 9999))
    return os.path.join(EDGE_PROFILE_BASE, f"profile_{ts}_{suffix}")

# En desuso, se mantiene como legacy solo para que quede constancia de la metodología anterior
def _get_chrome_major_version(exe_path: Optional[str]) -> Optional[int]:
    """Obtiene la versión mayor (p.ej. 144) de Chrome ejecutando "--version".
    Devuelve None si no se puede determinar.
    """
    try:
        if not exe_path or not os.path.exists(exe_path):
            return None
        out = subprocess.check_output([exe_path, "--version"], universal_newlines=True)
        # Ejemplos posibles:
        # "Google Chrome 144.0.7559.98"
        # "Chrome 144.0.7559.98"
        m = re.search(r"(\d+)\.\d+\.\d+\.\d+", out)
        if m:
            return int(m.group(1))
    except Exception:
        pass
    return None

# Esto usa selenium para abrir Chrome y luego Edge si Chrome llegase a fallar
def _selenium_warmup_and_transfer_cookies(sess) -> bool:
    """Abre Chrome (no headless) para resolver el reto y transfiere cookies a la sesión HTTP."""
    global _WARMUP_DONE
    global STABLE_UA
    # Por problemas varios con Chrome
    # # se comento todo rastro del uso de Chrome y se mantiene Edge
    # try:
    #     import undetected_chromedriver as uc  # type: ignore
    # except Exception as e:
    #     print(f"  Warm-up no disponible (UC no instalado): {e}")
    #     uc = None  # permitir fallback a Edge
    # Crear perfil persistente para evitar reto recurrente
    try:
        os.makedirs(PROFILE_DIR, exist_ok=True)
    except Exception:
        pass

    driver = None
    # Mas codigo creado para Chrome
    # if uc is not None:
    #     # Iniciar navegador NO headless para permitir interacción humana si aparece reto
    #     try:
    #         opts = uc.ChromeOptions()  # type: ignore
    #         # No usar headless aquí
    #         opts.add_argument("--lang=es-ES")
    #         opts.add_argument("--disable-blink-features=AutomationControlled")
    #         chrome_path = _find_chrome_exe()
    #         if chrome_path:
    #             # Deja que UC gestione el driver; no fijar version_main para evitar desajustes
    #             opts.add_argument(f"--user-data-dir={PROFILE_DIR}")
    #             major = _get_chrome_major_version(chrome_path)
    #             if major:
    #                 driver = uc.Chrome(options=opts, browser_executable_path=str(chrome_path), version_main=major)  # type: ignore
    #             else:
    #                 driver = uc.Chrome(options=opts, browser_executable_path=str(chrome_path))  # type: ignore
    #         else:
    #             opts.add_argument(f"--user-data-dir={PROFILE_DIR}")
    #             driver = uc.Chrome(options=opts)  # type: ignore
    #     except Exception as e:
    #         print(f"  No se pudo iniciar Chrome para warm-up: {e}")
    #         driver = None
    # # Fallback: Selenium Chrome; preferir Selenium Manager para autoajustar versión
    # if driver is None:
    #     try:
    #         from selenium import webdriver  # type: ignore
    #         from selenium.webdriver.chrome.service import Service as ChromeService  # type: ignore
    #         from selenium.webdriver.chrome.options import Options as ChromeOptions  # type: ignore
    #         from webdriver_manager.chrome import ChromeDriverManager  # type: ignore
    #         copts = ChromeOptions()
    #         copts.add_argument("--lang=es-ES")
    #         copts.add_argument("--disable-blink-features=AutomationControlled")
    #         copts.add_argument(f"--user-data-dir={PROFILE_DIR}")
    #         chrome_path2 = _find_chrome_exe()
    #         if chrome_path2:
    #             try:
    #                 copts.binary_location = chrome_path2
    #             except Exception:
    #                 pass
    #         # Intentar primero con Selenium Manager (auto detecta y descarga driver correcto)
    #         try:
    #             driver = webdriver.Chrome(options=copts)
    #             print("  Warm-up: usando Chrome con Selenium Manager.")
    #         except Exception as e_mgr:
    #             print(f"  Selenium Manager falló: {e_mgr}. Probando webdriver-manager…")
    #             service = ChromeService(ChromeDriverManager().install())
    #             driver = webdriver.Chrome(service=service, options=copts)
    #             print("  Warm-up: usando Chrome con webdriver-manager como fallback.")
    #     except Exception as e:
    #         print(f"  No se pudo iniciar Chrome via webdriver-manager: {e}")
    #         driver = None
    # Edge (driver local -> Selenium Manager -> webdriver-manager)
    
    # Aqui si arrancamos el wamp-up o abrir la sesion y hacer los Captchas en caso de ser necesario
    if driver is None:
        try:
            from selenium import webdriver  # type: ignore
            from selenium.webdriver.edge.options import Options as EdgeOptions  # type: ignore
            eopts = EdgeOptions()
            # No headless para permitir resolver reto
            eopts.add_argument("--lang=es-ES")
            eopts.add_argument("--disable-blink-features=AutomationControlled")
            profile_dir = _get_edge_profile_dir(use_temp=True)
            eopts.add_argument(f"--user-data-dir={profile_dir}")
            edge_bin = _find_edge_exe()
            if edge_bin:
                try:
                    eopts.binary_location = edge_bin
                except Exception:
                    pass
            edge_driver_path = _find_edgedriver_exe()
            if edge_driver_path:
                try:
                    from selenium.webdriver.edge.service import Service as EdgeService  # type: ignore
                    service = EdgeService(edge_driver_path, log_output=EDGE_DRIVER_LOG)
                    driver = webdriver.Edge(service=service, options=eopts)
                    print("  Warm-up: usando Edge con driver local.")
                except Exception as e_local:
                    print(f"  Driver local Edge falló: {e_local}")
                    driver = None
            if driver is None:
                try:
                    driver = webdriver.Edge(options=eopts)
                    print("  Warm-up: usando Edge con Selenium Manager.")
                except Exception as e_mgr:
                    print(f"  Selenium Manager falló: {e_mgr}. Probando webdriver-manager…")
                    from selenium.webdriver.edge.service import Service as EdgeService  # type: ignore
                    from webdriver_manager.microsoft import EdgeChromiumDriverManager  # type: ignore
                    service = EdgeService(EdgeChromiumDriverManager().install(), log_output=EDGE_DRIVER_LOG)
                    driver = webdriver.Edge(service=service, options=eopts)
                    print("  Warm-up: usando Edge con webdriver-manager como fallback.")
        except Exception as e:
            print(f"  No se pudo iniciar Edge para warm-up: {e}")
            driver = None
    # Mas codigo para Chrome
    # # Fallback a Edge con webdriver-manager si Chrome no está disponible
    # if driver is None:
    #     try:
    #         from selenium import webdriver  # type: ignore
    #         from selenium.webdriver.edge.service import Service as EdgeService  # type: ignore
    #         from selenium.webdriver.edge.options import Options as EdgeOptions  # type: ignore
    #         from webdriver_manager.microsoft import EdgeChromiumDriverManager  # type: ignore
    #         eopts = EdgeOptions()
    #         eopts.add_argument("--lang=es-ES")
    #         eopts.add_argument("--disable-blink-features=AutomationControlled")
    #         driver = webdriver.Edge(service=EdgeService(EdgeChromiumDriverManager().install()), options=eopts)
    #         print("  Warm-up: usando Edge con webdriver-manager como fallback.")
    #     except Exception as ee:
    #         print(f"  No se pudo iniciar Edge via webdriver-manager: {ee}")
    # # Fallback a Edge no headless si Chrome no está disponible
    # if driver is None:
    #     try:
    #         from selenium.webdriver import Edge  # type: ignore
    #         from selenium.webdriver.edge.options import Options as EdgeOptions  # type: ignore
    #         eopts = EdgeOptions()
    #         # No headless para permitir resolver reto
    #         eopts.add_argument("--lang=es-ES")
    #         eopts.add_argument("--disable-blink-features=AutomationControlled")
    #         driver = Edge(options=eopts)
    #         print("  Warm-up: usando Microsoft Edge como fallback.")
    #     except Exception as ee:
    #         print(f"  No se pudo iniciar Edge para warm-up: {ee}")
    #         return False

    # Este es el error si el captcha falla o se llega a actualizar Edge y el Edgedriver falla
    if driver is None:
        print("  Warm-up: no se pudo iniciar Edge. Abortando warm-up.")
        return False
    if driver is None:
        print("  Warm-up: no se pudo iniciar Edge. Abortando warm-up.")
        return False
    try:
        base_url = "https://fbref.com/en/"
        driver.get(base_url)
        print("  Warm-up: Se abrió el navegador. Si ves un reto, resuélvelo.")
        # Capturar el UA real del navegador para sincronizar las peticiones HTTP
        try:
            ua = driver.execute_script("return navigator.userAgent")
            if isinstance(ua, str) and ua.strip():
                STABLE_UA = ua.strip()
                os.environ["MY_UA"] = STABLE_UA
                print(f"  Warm-up: User-Agent sincronizado con Chrome: {STABLE_UA}")
        except Exception:
            pass
        # Esperar hasta que no existan marcadores de reto o timeout
        start = time.time()
        success = False
        print(f"  Warm-up: esperando verificación (~{WARMUP_TIMEOUT_SECONDS}s máx.)…")
        while time.time() - start < WARMUP_TIMEOUT_SECONDS:
            html = driver.page_source
            if not _is_challenge_response(html):
                success = True
                break
            time.sleep(1.0 + random.uniform(0.0, 1.5))
        # Navegar también al índice de fechas para asegurar cookies en esa ruta
        try:
            driver.get("https://fbref.com/en/matches/")
            time.sleep(0.8 + random.uniform(0.0, 1.0))
        except Exception:
            pass
        if not success:
            print("  Warm-up: Timeout sin confirmación de bypass; se intentará igualmente con cookies actuales.")
        # Transferir cookies al session
        try:
            cookies = driver.get_cookies()
            for c in cookies:
                try:
                    sess.cookies.set(
                        c.get("name"),
                        c.get("value"),
                        domain=c.get("domain"),
                        path=c.get("path", "/"),
                    )
                except Exception:
                    pass
            _WARMUP_DONE = True
            print(f"  Warm-up: Cookies transferidas ({len(cookies)}).")
            try:
                _save_session_cookies(sess)
            except Exception:
                pass
            try:
                _inject_manual_cf_clearance(sess)
            except Exception:
                pass
        except Exception as ce:
            print(f"  Warm-up: Error transfiriendo cookies: {ce}")
        return success
    finally:
        try:
            driver.quit()
        except Exception:
            pass

# El selenium creado para chrome
# def _selenium_fetch_html(url: str) -> Optional[str]:
#     """Obtiene HTML de una URL usando un navegador real con perfil persistente."""
#     driver = None
#     try:
#         import undetected_chromedriver as uc  # type: ignore
#         opts = uc.ChromeOptions()  # type: ignore
#         opts.add_argument("--lang=es-ES")
#         opts.add_argument("--disable-blink-features=AutomationControlled")
#         opts.add_argument(f"--user-data-dir={PROFILE_DIR}")
#         chrome_path = _find_chrome_exe()
#         if chrome_path:
#             major = _get_chrome_major_version(chrome_path)
#             if major:
#                 driver = uc.Chrome(options=opts, browser_executable_path=str(chrome_path), version_main=major)  # type: ignore
#             else:
#                 driver = uc.Chrome(options=opts, browser_executable_path=str(chrome_path))  # type: ignore
#         else:
#             driver = uc.Chrome(options=opts)  # type: ignore
#     except Exception:
#         # Fallback a Selenium Chrome; preferir Selenium Manager para autoajustar versión
#         try:
#             from selenium import webdriver  # type: ignore
#             from selenium.webdriver.chrome.service import Service as ChromeService  # type: ignore
#             from selenium.webdriver.chrome.options import Options as ChromeOptions  # type: ignore
#             from webdriver_manager.chrome import ChromeDriverManager  # type: ignore
#             copts = ChromeOptions()
#             copts.add_argument("--lang=es-ES")
#             copts.add_argument("--disable-blink-features=AutomationControlled")
#             copts.add_argument(f"--user-data-dir={PROFILE_DIR}")
#             chrome_path2 = _find_chrome_exe()
#             if chrome_path2:
#                 try:
#                     copts.binary_location = chrome_path2
#                 except Exception:
#                     pass
#             try:
#                 # Intentar con Selenium Manager primero
#                 driver = webdriver.Chrome(options=copts)
#                 print("  Selenium: usando Chrome con Selenium Manager.")
#             except Exception as e_mgr:
#                 print(f"  Selenium Manager falló: {e_mgr}. Probando webdriver-manager…")
#                 service = ChromeService(ChromeDriverManager().install())
#                 driver = webdriver.Chrome(service=service, options=copts)
#                 print("  Selenium: usando Chrome con webdriver-manager como fallback.")
#         except Exception:
#             # Fallback a Edge con webdriver-manager, luego Edge estándar
#             try:
#                 from selenium import webdriver  # type: ignore
#                 from selenium.webdriver.edge.service import Service as EdgeService  # type: ignore
#                 from selenium.webdriver.edge.options import Options as EdgeOptions  # type: ignore
#                 from webdriver_manager.microsoft import EdgeChromiumDriverManager  # type: ignore
#                 eopts = EdgeOptions()
#                 eopts.add_argument("--lang=es-ES")
#                 eopts.add_argument("--disable-blink-features=AutomationControlled")
#                 driver = webdriver.Edge(service=EdgeService(EdgeChromiumDriverManager().install()), options=eopts)
#                 print("  Selenium: usando Edge con webdriver-manager como fallback.")
#             except Exception:
#                 try:
#                     from selenium.webdriver import Edge  # type: ignore
#                     from selenium.webdriver.edge.options import Options as EdgeOptions  # type: ignore
#                     eopts = EdgeOptions()
#                     eopts.add_argument("--lang=es-ES")
#                     eopts.add_argument("--disable-blink-features=AutomationControlled")
#                     driver = Edge(options=eopts)
#                 except Exception as ee:
#                     print(f"  Selenium fallback no disponible: {ee}")
#                     return None
#     try:
#         driver.get(url)
#         # Espera básica de carga
#         time.sleep(1 + random.uniform(1, 8))
#         # Sincronizar UA si aún no lo tenemos exacto
#         try:
#             ua = driver.execute_script("return navigator.userAgent")
#             if isinstance(ua, str) and ua.strip():
#                 # Actualizar solo si cambia
#                 if ua.strip() != STABLE_UA:
#                     STABLE_UA = ua.strip()
#                     os.environ["MY_UA"] = STABLE_UA
#                     print(f"  Selenium: User-Agent actualizado: {STABLE_UA}")
#         except Exception:
#             pass
#         return driver.page_source
#     except Exception as e:
#         print(f"  Error Selenium al obtener {url}: {e}")
#         return None
#     finally:
#         try:
#             driver.quit()
#         except Exception:
#             pass

# El selenium de Edge
def _selenium_fetch_html(url: str) -> Optional[str]:
    """Obtiene HTML de una URL usando Microsoft Edge con perfil persistente."""
    global STABLE_UA
    driver = None
    try:
        from selenium import webdriver  # type: ignore
        from selenium.webdriver.edge.options import Options as EdgeOptions  # type: ignore
        eopts = EdgeOptions()
        eopts.add_argument("--lang=es-ES")
        eopts.add_argument("--disable-blink-features=AutomationControlled")
        profile_dir = _get_edge_profile_dir(use_temp=True)
        eopts.add_argument(f"--user-data-dir={profile_dir}")
        edge_bin = _find_edge_exe()
        if edge_bin:
            try:
                eopts.binary_location = edge_bin
            except Exception:
                pass
        edge_driver_path = _find_edgedriver_exe()
        if edge_driver_path:
            try:
                from selenium.webdriver.edge.service import Service as EdgeService  # type: ignore
                service = EdgeService(edge_driver_path, log_output=EDGE_DRIVER_LOG)
                driver = webdriver.Edge(service=service, options=eopts)
                print("  Selenium: usando Edge con driver local.")
            except Exception as e_local:
                print(f"  Driver local Edge falló: {e_local}")
                driver = None
        if driver is None:
            try:
                driver = webdriver.Edge(options=eopts)
                print("  Selenium: usando Edge con Selenium Manager.")
            except Exception as e_mgr:
                print(f"  Selenium Manager falló: {e_mgr}. Probando webdriver-manager…")
                from selenium.webdriver.edge.service import Service as EdgeService  # type: ignore
                from webdriver_manager.microsoft import EdgeChromiumDriverManager  # type: ignore
                service = EdgeService(EdgeChromiumDriverManager().install(), log_output=EDGE_DRIVER_LOG)
                driver = webdriver.Edge(service=service, options=eopts)
                print("  Selenium: usando Edge con webdriver-manager como fallback.")
    except Exception as ee:
        print(f"  Selenium Edge no disponible: {ee}")
        return None
    if driver is None:
        print("  Selenium: no se pudo iniciar Edge.")
        return None
    try:
        driver.get(url)
        # Espera básica de carga
        time.sleep(0.8 + random.uniform(0.0, 1.0))
        # Sincronizar UA si aún no lo tenemos exacto
        try:
            ua = driver.execute_script("return navigator.userAgent")
            if isinstance(ua, str) and ua.strip():
                # Actualizar solo si cambia
                if ua.strip() != STABLE_UA:
                    STABLE_UA = ua.strip()
                    os.environ["MY_UA"] = STABLE_UA
                    print(f"  Selenium: User-Agent actualizado: {STABLE_UA}")
        except Exception:
            pass
        return driver.page_source
    except Exception as e:
        print(f"  Error Selenium al obtener {url}: {e}")
        return None
    finally:
        try:
            driver.quit()
        except Exception:
            pass

# La funcion que permite llegar a la pagina, obtener las cookies en un json para inyectarlas a Selenium
def _save_session_cookies(sess) -> None:
    try:
        jar = getattr(sess, 'cookies', None)
        if not jar:
            return
        rows = []
        # Intentar iterar como objetos tipo Cookie (requests/http.cookiejar)
        try:
            for c in jar:
                name = getattr(c, 'name', None)
                value = getattr(c, 'value', None)
                domain = getattr(c, 'domain', None)
                path = getattr(c, 'path', None)
                expires = getattr(c, 'expires', None)
                # Si la iteración devuelve strings o tuplas, adaptarlo
                if isinstance(c, str):
                    name = c
                    try:
                        value = jar.get(c)
                    except Exception:
                        value = None
                    domain = None
                    path = '/'
                    expires = None
                elif isinstance(c, tuple) and len(c) >= 2:
                    name = c[0]
                    value = c[1]
                    domain = None
                    path = '/'
                    expires = None
                if name is not None:
                    rows.append({
                        'name': name,
                        'value': value,
                        'domain': domain,
                        'path': path,
                        'expires': expires,
                    })
        except Exception:
            pass
        # Fallback: si no se pudo iterar correctamente, usar get_dict()
        if not rows:
            try:
                for name, value in (jar.get_dict() or {}).items():
                    rows.append({
                        'name': name,
                        'value': value,
                        'domain': None,
                        'path': '/',
                        'expires': None,
                    })
            except Exception:
                pass
        df = pd.DataFrame(rows)
        df.to_json(COOKIES_FILE, orient='records')
        print(f"  Cookies guardadas: {COOKIES_FILE}")
    except Exception as e:
        print(f"  No se pudieron guardar cookies: {e}")

# Cargamos las cookies desde el json que la funcion anterior crea
# Esto funciona igual tanto en Edge como en Chrome, pues ambos son Chromium
def _load_session_cookies(sess) -> None:
    try:
        if not os.path.exists(COOKIES_FILE):
            return
        import json
        with open(COOKIES_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        cnt = 0
        for c in data:
            try:
                sess.cookies.set(c.get('name'), c.get('value'), domain=c.get('domain'), path=c.get('path') or '/')
                cnt += 1
            except Exception:
                pass
        print(f"  Cookies cargadas: {cnt}")
    except Exception as e:
        print(f"  No se pudieron cargar cookies: {e}")

# Si el  json llgase a fallar, se usa el clearance previamente insertado manualmente en la terminal
def _inject_manual_cf_clearance(sess) -> None:
    """Inyecta manualmente la cookie cf_clearance si está definida en env var 'FBREF_CF_CLEARANCE'."""
    try:
        val = os.environ.get("FBREF_CF_CLEARANCE")
        if not val:
            return
        # Dominio base de fbref; Cloudflare suele marcar en fbref.com
        sess.cookies.set("cf_clearance", val, domain="fbref.com", path="/")
        print("  cf_clearance manual inyectada en la sesión.")
    except Exception as e:
        print(f"  No se pudo inyectar cf_clearance: {e}")

# 
def _request(url: str, referer: Optional[str] = None, timeout: int = 25):
    global CONSEC_BLOCKS
    _sleep_until_allowed()
    _enforce_window_limit()
    headers = _build_headers(referer) #construimos lo headers
    try:
        # curl_cffi Session y requests.Session comparten API .get
        resp = SESSION.get(url, headers=headers, timeout=timeout, allow_redirects=True)
    # Detectamos el tipo de error
    except Exception as e: 
        print(f"  Error de conexión a {url}: {e}")
        _log_failed_url(url, status=None, reason=f"exception: {type(e).__name__}", context={'stage': 'request'})
        _schedule_next(10 + random.uniform(0, 2))
        return None

    # Si es error 200 pero contiene reto, tratar como bloqueo
    if resp.status_code == 200:
        txt = getattr(resp, 'text', '')
        if txt and _is_challenge_response(txt):
            print("  Respuesta incluye reto. Intentando warm-up y reintento…")
            CONSEC_BLOCKS += 1
        else:
            CONSEC_BLOCKS = 0
            _schedule_next()
            return resp
    # Si es error 403/429 de bloqueo, iniciar una sesion warmup si es necesario
    if resp.status_code in (403, 429) or _is_challenge_response(getattr(resp, 'text', '')):
        print(f"  Status {resp.status_code} o reto detectado. Iniciando warm-up si es necesario…")
        CONSEC_BLOCKS += 1
        force_warmup = (CONSEC_BLOCKS >= _BLOCK_THRESHOLD)
        if force_warmup or not _WARMUP_DONE:
            if force_warmup:
                print(f"  Bloqueos consecutivos={CONSEC_BLOCKS} ≥ {_BLOCK_THRESHOLD}. Ejecutando warm-up de emergencia…")
            ok = _selenium_warmup_and_transfer_cookies(SESSION)
            if ok:
                CONSEC_BLOCKS = 0
                # Reintento inmediato (una vez)
                try:
                    _schedule_next(3 + random.uniform(0, 2))
                    headers = _build_headers(referer)
                    resp2 = SESSION.get(url, headers=headers, timeout=timeout, allow_redirects=True)
                    if resp2.status_code == 200 and not _is_challenge_response(getattr(resp2, 'text', '')):
                        _schedule_next()
                        return resp2
                    else:
                        print(f"  Reintento tras warm-up falló: status={resp2.status_code}")
                except Exception as e:
                    print(f"  Error en reintento tras warm-up: {e}")
        wait = 60 + random.uniform(0, 10)
        print(f"  Bloqueo persiste. Esperando {int(wait)}s…")
        _log_failed_url(url, status=resp.status_code, reason='blocked', context={'stage': 'request'})
        _schedule_next(wait)
        return None
    # Si se consigue un error 5xx se prodece de esta manera
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

# Esta funcion permite la introduccion de fechas usando el formato yyyy-mm-dd
# Permite variar el rango de fechas para descarga de datos
def daterange(start_date: str, end_date: str):
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    for n in range(int((end - start).days) + 1):
        yield (start + timedelta(n)).strftime("%Y-%m-%d")


def _extract_match_index_date_from_url(url: Optional[str]) -> Optional[str]:
    """Extrae YYYY-MM-DD desde URLs de índice diario o de partido.

    Soporta:
    - /en/matches/YYYY-MM-DD
    - /en/matches/<match_id>/<slug-con-Month-DD-YYYY>
    """
    if not url:
        return None
    u = str(url)

    # Caso 1: URL de índice diario.
    m_daily = re.search(r"/en/matches/(\d{4}-\d{2}-\d{2})(?:$|[/?#])", u)
    if m_daily:
        return m_daily.group(1)

    # Caso 2: URL de partido con fecha en slug: Month-DD-YYYY.
    m_slug = re.search(
        r"-(January|February|March|April|May|June|July|August|September|October|November|December)-(\d{1,2})-(\d{4})(?:-|$)",
        u,
        flags=re.IGNORECASE,
    )
    if not m_slug:
        return None

    month = m_slug.group(1).title()
    day = m_slug.group(2)
    year = m_slug.group(3)
    try:
        dt = datetime.strptime(f"{month}-{day}-{year}", "%B-%d-%Y")
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return None


def _find_failed_url_csv_files(base_path: str) -> List[str]:
    """Lista CSVs de fallos con patrón failed_url_YYYYMMDD_HHMMSS.csv."""
    files: List[Tuple[str, str]] = []
    try:
        for name in os.listdir(base_path):
            m = FAILED_URL_CSV_REGEX.match(name)
            if not m:
                continue
            ts = f"{m.group(1)}_{m.group(2)}"
            files.append((ts, os.path.join(base_path, name)))
    except Exception:
        return []
    files.sort(key=lambda x: x[0], reverse=True)
    return [p for _, p in files]


def _load_failed_dates_from_csvs(base_path: str) -> List[str]:
    """Carga fechas únicas desde CSVs failed_url_* leyendo la columna 'url'."""
    files = _find_failed_url_csv_files(base_path)
    if not files:
        print("No se encontraron archivos failed_url_YYYYMMDD_HHMMSS.csv en la carpeta del script.")
        return []

    dates = set()
    date_hits = 0
    inspected = 0
    for path in files:
        inspected += 1
        try:
            df = pd.read_csv(path)
        except Exception as e:
            print(f"  No se pudo leer {os.path.basename(path)}: {e}")
            continue

        if 'url' not in df.columns:
            print(f"  Archivo omitido (sin columna 'url'): {os.path.basename(path)}")
            continue

        for raw_url in df['url'].dropna().astype(str).tolist():
            d = _extract_match_index_date_from_url(raw_url)
            if d:
                date_hits += 1
                dates.add(d)

    out = sorted(dates)
    duplicates_removed = max(0, date_hits - len(out))
    print(
        f"CSVs inspeccionados: {inspected}; fechas detectadas: {date_hits}; "
        f"fechas únicas: {len(out)}; repetidas eliminadas: {duplicates_removed}"
    )
    return out

# Permite normalizar nombres de equipos, mantiene la capitalizacion
def _normalize_team(name: Optional[str]) -> Optional[str]:
    if not name:
        return name
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.category(c) == "Mn")  # quitar tildes
    s = s.replace("\xa0", " ")  # NBSP
    s = " ".join(s.split())  # espacios repetidos
    # No forzar case que rompa acrónimos; solo recortar
    return s.strip()

# normaliza texto en general, pero convierte a minusculas
def _normalize_text(value: Optional[str]) -> str:
    if not value:
        return ""
    s = unicodedata.normalize("NFKD", value)
    s = "".join(c for c in s if not unicodedata.category(c) == "Mn")
    s = s.replace("\xa0", " ")
    s = " ".join(s.split())
    return s.strip().lower()


def _league_matches(league_name: Optional[str]) -> bool:
    if not league_name:
        return False
    norm = _normalize_text(league_name)
    if any(pat in norm for pat in LEAGUE_EXCLUDE_PATTERNS):
        return False
    return any(pat in norm for pat in LEAGUE_PATTERNS)

# Esta funcion es el "segundo barrido", obtiene la lista de partidos
# usa la lista de ligas y el periodo seleccionado
def fetch_matches_for_date(date: str) -> List[Dict]:
    """Extrae partidos desde el índice diario: columnas mínimas + raw_row."""
    url = BASE_URL + date
    resp = _request(url, referer="https://fbref.com/")
    soup = None
    if resp is None:
        # Fallback a navegador real con perfil persistente
        html = _selenium_fetch_html(url)
        if not html:
            print(f"  No se pudo acceder al índice del día {date}")
            _log_failed_url(url, status=None, reason='index_failed', context={'stage': 'index', 'date': date})
            return []
        soup = BeautifulSoup(html, "html.parser")
    else:
        soup = BeautifulSoup(resp.text, "html.parser")

    _soft_pause(INTER_MATCH_SLEEP_RANGE)
    out: List[Dict] = []

    for table in soup.find_all("table"):
        caption = table.find("caption")
        league_name = caption.text.strip() if caption else None
        if not _league_matches(league_name):
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

    return out


# Extracción de tablas de estadísticas por jugador en página de partido
# Codigo de legado, descargaba las estadisticas opta de estar disponibles
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

# toma el id y extrae el nombre y ripo de tabla
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

# aplana columnas multi-index a un solo index
def _flatten_columns(df: pd.DataFrame) -> pd.DataFrame:
    if any(isinstance(c, tuple) for c in df.columns):
        def _flat(c):
            if isinstance(c, tuple):
                return ' '.join([str(x) for x in c if x is not None]).strip()
            return str(c)
        df.columns = [_flat(c) for c in df.columns]
    return df

# Convierte fechas a strings usando el formato estandar japones YYYY-MM-DD
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

# funcion para limpiar los caracteres que podrian ocasionar traceback
def _safe_filename(text: Optional[str]) -> str:
    """Sanear texto para usarlo en nombres de archivo (Windows-friendly)."""
    if not text:
        return ""
    s = unicodedata.normalize("NFKD", str(text))
    s = "".join(c for c in s if not unicodedata.category(c) == "Mn")
    s = s.replace("\xa0", " ")
    s = " ".join(s.split())
    # Reemplazar caracteres inválidos de Windows y separadores de ruta
    s = re.sub(r'[<>:"/\\|?*]', "_", s)
    s = s.replace(".", "_")
    s = s.strip(" .")
    s = s.replace(" ", "_")
    return s

# Construye los df que se exportaran como csv en la modalidad players
def _base_filename_players(match: Dict) -> str:
    """Construye el nombre base esperado para archivos de tablas de jugador."""
    date_str = _to_ymd(match.get('date', ''))
    try:
        fecha = datetime.strptime(date_str, '%Y-%m-%d').strftime('%d-%m-%Y')
    except Exception:
        fecha = date_str
    league_short = _safe_filename((match.get('league') or '').split('Table')[0].strip())
    home = _safe_filename(match.get('home_team') or '')
    away = _safe_filename(match.get('away_team') or '')
    return f"players_{league_short}-{fecha}-{home}-vs_{away}"

# Construye los df que se exportaran como csv en la modalidad all
def _base_filename_all(match: Dict) -> str:
    """Construye el nombre base esperado para archivos de todas las tablas del partido."""
    date_str = _to_ymd(match.get('date', ''))
    try:
        fecha = datetime.strptime(date_str, '%Y-%m-%d').strftime('%d-%m-%Y')
    except Exception:
        fecha = date_str
    league_short = _safe_filename((match.get('league') or '').split('Table')[0].strip())
    home = _safe_filename(match.get('home_team') or '')
    away = _safe_filename(match.get('away_team') or '')
    return f"all_{league_short}-{fecha}-{home}-vs_{away}"

# Detecta si hubo algun error en el archivo players
# Si lo hay, descarga de nuevo
def _existing_output_for_players(match: Dict, base_path: str, save_mode: str) -> Optional[str]:
    base_filename = _base_filename_players(match)
    if save_mode == 'excel':
        path = os.path.join(base_path, base_filename + '.xlsx')
        if os.path.exists(path):
            try:
                if os.path.getsize(path) < MIN_VALID_FILE_BYTES:
                    print(f"    Archivo incompleto detectado (players excel): {os.path.basename(path)}. Se borra y reintenta.")
                    os.remove(path)
                    return None
            except Exception:
                pass
            return path
        return None
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

# Detecta si hubo algun error en el archivo all
# Si lo hay, descarga de nuevo
def _existing_output_for_all(match: Dict, base_path: str, save_mode: str) -> Optional[str]:
    base_filename = _base_filename_all(match)
    if save_mode == 'excel':
        path = os.path.join(base_path, base_filename + '.xlsx')
        if os.path.exists(path):
            try:
                if os.path.getsize(path) < MIN_VALID_FILE_BYTES:
                    print(f"    Archivo incompleto detectado (all excel): {os.path.basename(path)}. Se borra y reintenta.")
                    os.remove(path)
                    return None
            except Exception:
                pass
            return path
        return None
    elif save_mode == 'csv_unico':
        path = os.path.join(base_path, base_filename + '.csv')
        if os.path.exists(path):
            try:
                if os.path.getsize(path) < MIN_VALID_FILE_BYTES:
                    print(f"    Archivo incompleto detectado (all csv): {os.path.basename(path)}. Se borra y reintenta.")
                    os.remove(path)
                    return None
            except Exception:
                pass
            return path
        return None
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

# Verifica los manifiestos
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
                try:
                    if os.path.getsize(p) >= MIN_VALID_FILE_BYTES:
                        existing += 1
                    else:
                        print(f"      Archivo listado en manifiesto demasiado pequeño: {f}. Se considerará faltante.")
                        try:
                            os.remove(p)
                        except Exception:
                            pass
                except Exception:
                    existing += 1
    except Exception:
        # Si hay error al iterar, considerarlo incompleto
        pass
    return (existing == total and total > 0), existing, total

# Descarga la data en modo players y lo exporta en csv
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

    resp = _request(url, referer=daily_referer)
    if resp is None:
        # Fallback: usar navegador real para capturar HTML directamente
        html = _selenium_fetch_html(url)
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
    else:
        soup = BeautifulSoup(resp.text, 'html.parser')
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
        _soft_pause(LOCAL_PARSE_SLEEP_RANGE)

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
        _soft_pause(LOCAL_PARSE_SLEEP_RANGE)
        return filepath
    elif save_mode == 'csv':
        manifest = []
        last_path = ''
        for info in infos:
            idx = info['idx']
            team = _safe_filename(info['team'] or 'team')
            kind = _safe_filename(info['kind'] or 'kind')
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
        _soft_pause(LOCAL_PARSE_SLEEP_RANGE)
        return man_path
    else:
        print(f"    Modo '{save_mode}' no reconocido. Omite guardado.")
        return ''


# Extracción de TODAS las tablas de la página del partido (como V2/V1, robusto)
# Codigo de legado de versiones anteriores descarga en modo ALL
# creado para descargar en formato OPTA
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

    resp = _request(url, referer=daily_referer)
    if resp is None:
        # Fallback: usar navegador real para capturar HTML directamente
        html = _selenium_fetch_html(url)
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
    else:
        soup = BeautifulSoup(resp.text, 'html.parser')
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
        _soft_pause(LOCAL_PARSE_SLEEP_RANGE)

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
        _soft_pause(LOCAL_PARSE_SLEEP_RANGE)
        return filepath
    elif save_mode == 'csv_unico':
        df_all = pd.concat(dfs, axis=0, ignore_index=True)
        filename = base_filename + '.csv'
        filepath = os.path.join(base_path, filename)
        df_all.to_csv(filepath, index=False)
        print(f"    Guardado CSV único (todas las tablas): {filename}")
        _soft_pause(LOCAL_PARSE_SLEEP_RANGE)
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
        _soft_pause(LOCAL_PARSE_SLEEP_RANGE)
        return man_path
    else:
        print(f"    Modo '{save_mode}' no reconocido. Omite guardado.")
        return ''

# Construye el archivo master
# Esto es utilizable para hacer correr el modelo de Shelopugi
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

# Reintenta las descargas fallidas
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
        _soft_pause(INTER_MATCH_SLEEP_RANGE)
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

# Aquellas que no se pudieron descargar en la funcion anterior se exportan como csv
def export_failure_logs(base_path: str) -> Tuple[Optional[str], Optional[str]]:
    """Exporta CSVs de fallos: URLs y partidos con problemas. Devuelve rutas."""
    url_path = match_path = None
    try:
        if FAILED_URL_LOG:
            df_urls = pd.DataFrame(FAILED_URL_LOG)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            # Formato alineado al patrón esperado por el flujo de reintentos
            url_path = os.path.join(base_path, f"failed_url_{ts}.csv")
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

# Devuelve datos descargados y valida que se hayan descargado correctamente
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

# Funcion principal, pone todo a funcionar
def main():
    print("=== ScraperV4 FBref (índice diario → dataset maestro) ===")

    # Qué descargar.
    # Opciones: 1=maestro, 2=maestro+jugador, 3=maestro+todas, 4=todo,
    #           5=solo_jugador, 6=solo_todas
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

    # Fuente de partidos a procesar.
    # 1=rango de fechas, 2=failed_url_YYYYMMDD_HHMMSS.csv
    source_in = input(
        "Fuente de partidos: [1] rango_fechas, [2] failed_url_csv (ENTER=1): "
    ).strip().lower() or '1'
    use_failed_url_csv = source_in in ('2', 'failed_url_csv', 'failed_url')

    start_date = DEFAULT_START_DATE
    end_date = DEFAULT_END_DATE
    if not use_failed_url_csv:
        start_input = input(
            f"Fecha de inicio (yyyy-mm-dd, ENTER = {DEFAULT_START_DATE}): "
        ).strip()
        start_date = start_input or DEFAULT_START_DATE

        end_input = input(
            f"Fecha de fin   (yyyy-mm-dd, ENTER = {DEFAULT_END_DATE}): "
        ).strip()
        end_date = end_input or DEFAULT_END_DATE

    # Formatos por defecto para cada tipo (CSV por defecto en todo)
    master_out_fmt = 'csv'            # maestro: CSV por defecto
    player_mode = 'csv'               # tablas de jugador: CSV por tabla + manifiesto
    all_mode = 'csv_por_tabla'        # todas las tablas: CSV por tabla + manifiesto

    base_path = os.path.dirname(os.path.abspath(__file__))
    print(f"Carpeta de salida: {base_path}")

    all_matches: List[Dict] = []

    if use_failed_url_csv:
        dates_to_process = _load_failed_dates_from_csvs(base_path)
        if not dates_to_process:
            print("No hay fechas válidas en los archivos failed_url_*.csv.")
            return
        print(f"Procesando fechas desde failed_url CSVs: {len(dates_to_process)}")
    else:
        dates_to_process = list(daterange(start_date, end_date))

    for date in dates_to_process:
        print(f"Procesando fecha: {date}")
        # Warm-up opcional al inicio de cada día si se habilita por env var
        if FORCE_DAILY_WARMUP:
            try:
                print("  Daily warm-up habilitado. Abriendo Chrome para sincronizar cookies/UA…")
                _selenium_warmup_and_transfer_cookies(SESSION)
            except Exception as e:
                print(f"  Daily warm-up falló: {e}")
        day_matches = fetch_matches_for_date(date)
        print(f"  Partidos encontrados: {len(day_matches)}")
        all_matches.extend(day_matches)
        _soft_pause(INTER_DAY_SLEEP_RANGE)  # pausa breve entre días para evitar ráfagas

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
            _soft_pause(INTER_MATCH_SLEEP_RANGE)

    # Extracción opcional de todas las tablas por partido
    if run_all:
        print("\n=== Extrayendo TODAS las tablas por partido ===")
        for m in df_master.to_dict(orient='records'):
            path = download_all_match_tables(m, base_path, save_mode=all_mode)
            m['_all_tables_path'] = path
            _soft_pause(INTER_MATCH_SLEEP_RANGE)

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