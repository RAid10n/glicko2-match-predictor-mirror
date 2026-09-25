import argparse
import csv
import datetime as dt
import json
import os
import random
import re
import ssl
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = "https://archive-api.open-meteo.com/v1/archive"
REQUEST_TIMEOUT_SECONDS = 60
HOURLY_VARS = [
    "temperature_2m",
    "relative_humidity_2m",
    "surface_pressure",
]
DERIVED_VARS = [
    "ph2o_hpa",
]


def safe_filename(text: str) -> str:
    """Return a filesystem-safe ASCII filename fragment."""
    text = text.strip()
    text = re.sub(r"[^A-Za-z0-9_-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "item"


def build_date_targets(start_year: int, start_month: int, end_year: int, end_month: int):
    targets = []
    year, month = start_year, start_month
    while (year, month) <= (end_year, end_month):
        targets.append(dt.date(year, month, 15))
        if month == 12:
            year += 1
            month = 1
        else:
            month += 1
    return targets


def fetch_hourly(
    lat: float,
    lon: float,
    start_date: str,
    end_date: str,
    retry_sleep: float,
    max_retries: int,
    error_retry_sleep: float,
):
    params = {
        "latitude": f"{lat:.6f}",
        "longitude": f"{lon:.6f}",
        "start_date": start_date,
        "end_date": end_date,
        "hourly": ",".join(HOURLY_VARS),
        "timezone": "auto",
    }
    url = f"{BASE_URL}?{urllib.parse.urlencode(params)}"
    attempts = 0
    while attempts <= max_retries:
        try:
            if os.name == "nt":
                curl_cmd = [
                    "curl.exe",
                    "-fsSL",
                    "--max-time",
                    str(REQUEST_TIMEOUT_SECONDS),
                    url,
                ]
                completed = subprocess.run(
                    curl_cmd,
                    capture_output=True,
                    text=True,
                    check=True,
                )
                return json.loads(completed.stdout)

            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
                payload = resp.read().decode("utf-8")
                return json.loads(payload)
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                attempts += 1
                if attempts > max_retries:
                    raise RuntimeError(f"Open-Meteo rate limit exceeded after {max_retries} retries") from exc
                print(f"  429 Too Many Requests. Sleeping {retry_sleep:.0f}s before retrying ({attempts}/{max_retries})...")
                time.sleep(retry_sleep)
                continue
            attempts += 1
            if attempts > max_retries:
                raise RuntimeError(f"HTTP {exc.code} after {max_retries} retries") from exc
            print(f"  HTTP {exc.code}. Retrying {attempts}/{max_retries} after {error_retry_sleep:.0f}s...")
            time.sleep(error_retry_sleep)
            continue
        except urllib.error.URLError as exc:
            attempts += 1
            if attempts > max_retries:
                raise RuntimeError(f"Network error after {max_retries} retries: {exc.reason}") from exc
            print(f"  Network error. Retrying {attempts}/{max_retries} after {error_retry_sleep:.0f}s...")
            time.sleep(error_retry_sleep)
            continue
        except ssl.SSLError as exc:
            attempts += 1
            if attempts > max_retries:
                raise RuntimeError(f"SSL error after {max_retries} retries: {exc}") from exc
            print(f"  SSL error. Retrying {attempts}/{max_retries} after {error_retry_sleep:.0f}s...")
            time.sleep(error_retry_sleep)
            continue
        except subprocess.CalledProcessError as exc:
            attempts += 1
            stderr_text = exc.stderr.strip() if exc.stderr else ""
            if attempts > max_retries:
                raise RuntimeError(f"curl failed after {max_retries} retries: {stderr_text or exc.returncode}") from exc
            print(f"  curl failed. Retrying {attempts}/{max_retries} after {error_retry_sleep:.0f}s...")
            time.sleep(error_retry_sleep)
            continue
        except FileNotFoundError:
            if os.name == "nt":
                raise RuntimeError("curl.exe not found on this system")
            raise
        except json.JSONDecodeError as exc:
            attempts += 1
            if attempts > max_retries:
                raise RuntimeError(f"Invalid JSON response from Open-Meteo: {exc}") from exc
            print(f"  Invalid JSON response. Retrying {attempts}/{max_retries} after {error_retry_sleep:.0f}s...")
            time.sleep(error_retry_sleep)
            continue
    raise RuntimeError("Unexpected fetch loop exit")


def saturation_vapor_pressure_hpa(temp_c: float) -> float:
    # Magnus formula, valid for typical ambient temperatures.
    return 6.112 * (2.718281828 ** ((17.67 * temp_c) / (temp_c + 243.5)))


def compute_ph2o_hpa(temp_c: float, rel_humidity: float) -> float:
    sat = saturation_vapor_pressure_hpa(temp_c)
    return sat * (rel_humidity / 100.0)


def extract_rows(data: dict, target_dates: set, target_hours: list[int]):
    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    values = {k: hourly.get(k, []) for k in HOURLY_VARS}

    hours_set = set(target_hours)
    rows_by_date = {}

    for i, ts in enumerate(times):
        # Example: 2021-08-15T18:00
        try:
            date_part, time_part = ts.split("T")
            hour = int(time_part.split(":")[0])
        except ValueError:
            continue

        if hour not in hours_set:
            continue
        try:
            date_obj = dt.date.fromisoformat(date_part)
        except ValueError:
            continue
        if date_obj not in target_dates:
            continue

        bucket = rows_by_date.setdefault(date_part, {k: [] for k in HOURLY_VARS + DERIVED_VARS})
        for k in HOURLY_VARS:
            series = values.get(k, [])
            if i < len(series):
                bucket[k].append(series[i])

        try:
            temp_c = values.get("temperature_2m", [])[i]
            rh = values.get("relative_humidity_2m", [])[i]
        except IndexError:
            temp_c, rh = None, None
        if temp_c is not None and rh is not None:
            bucket["ph2o_hpa"].append(compute_ph2o_hpa(temp_c, rh))

    rows = []
    label = f"{min(target_hours):02d}:00-{max(target_hours):02d}:00"
    for date_part in sorted(rows_by_date.keys()):
        row = {
            "date": date_part,
            "time": label,
        }
        for k in HOURLY_VARS + DERIVED_VARS:
            series = rows_by_date[date_part].get(k, [])
            if series:
                row[k] = sum(series) / len(series)
            else:
                row[k] = None
        rows.append(row)

    return rows


def read_locations(csv_path: str):
    encodings = ["utf-8", "utf-8-sig", "cp1252", "latin-1"]
    last_error = None
    for enc in encodings:
        try:
            with open(csv_path, "r", encoding=enc, newline="") as f:
                sample = f.read(4096)
                f.seek(0)
                try:
                    dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
                except csv.Error:
                    dialect = csv.get_dialect("excel")
                reader = csv.DictReader(f, dialect=dialect)
                for row in reader:
                    country = row.get("Country", "").strip()
                    team = row.get("Team", "").strip()
                    lat_raw = row.get("Lat", "").strip()
                    lon_raw = row.get("Lon", "").strip()
                    if not lat_raw or not lon_raw:
                        continue
                    try:
                        lat = float(lat_raw)
                        lon = float(lon_raw)
                    except ValueError:
                        continue
                    yield country, team, lat, lon
            return
        except UnicodeDecodeError as exc:
            last_error = exc
            continue
    if last_error:
        raise last_error


def write_csv(path: str, rows: list, elevation: float):
    fieldnames = [
        "date",
        "time",
        "temperature_2m",
        "relative_humidity_2m",
        "surface_pressure",
        "ph2o_hpa",
        "elevation",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            row_out = dict(row)
            row_out["elevation"] = elevation
            writer.writerow(row_out)


def main():
    parser = argparse.ArgumentParser(description="Download Open-Meteo historical data for 15th of each month at 18:00 local time.")
    parser.add_argument("--input", default=r"C:\Users\ruben\Desktop\TEG\EstadiumsDB.csv", help="Input CSV path")
    parser.add_argument("--output", default=r"C:\Users\ruben\Documents\Universidad\TEG\Codigos\VSCode\open_meteo_out", help="Output folder")
    parser.add_argument("--start", default="2017-08-01", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", default="2025-12-31", help="End date (YYYY-MM-DD)")
    parser.add_argument("--hour", type=int, default=18, help="Target local hour (0-23)")
    parser.add_argument("--hour-start", type=int, help="Start hour for averaging window (0-23)")
    parser.add_argument("--hour-end", type=int, help="End hour for averaging window (0-23)")
    parser.add_argument("--sleep", type=float, default=0.0, help="Base seconds to sleep between requests")
    parser.add_argument("--retry-sleep", type=float, default=4000.0, help="Seconds to sleep after HTTP 429 before retrying")
    parser.add_argument("--max-retries", type=int, default=3, help="Max retries for non-429 errors")
    parser.add_argument("--error-retry-sleep", type=float, default=10.0, help="Seconds to sleep between non-429 retries")
    args = parser.parse_args()

    os.makedirs(args.output, exist_ok=True)

    start_date = dt.date.fromisoformat(args.start)
    end_date = dt.date.fromisoformat(args.end)
    targets = build_date_targets(start_date.year, start_date.month, end_date.year, end_date.month)
    target_set = set(targets)
    print(f"Requested range: {args.start} -> {args.end} ({len(targets)} monthly target dates)")

    if args.hour_start is not None and args.hour_end is not None:
        hour_start = max(0, min(23, args.hour_start))
        hour_end = max(0, min(23, args.hour_end))
        if hour_start > hour_end:
            hour_start, hour_end = hour_end, hour_start
        target_hours = list(range(hour_start, hour_end + 1))
    else:
        target_hours = [max(0, min(23, args.hour))]

    failed = []

    for country, team, lat, lon in read_locations(args.input):
        name = safe_filename(f"{country}_{team}")
        out_path = os.path.join(args.output, f"{name}.csv")
        if os.path.exists(out_path):
            print(f"Skipping {country} / {team} (already exists)")
            continue
        print(f"Fetching {country} / {team} ({lat}, {lon}) -> {out_path}")

        try:
            data = fetch_hourly(
                lat,
                lon,
                args.start,
                args.end,
                args.retry_sleep,
                args.max_retries,
                args.error_retry_sleep,
            )
        except Exception as exc:
            print(f"  Error: {exc}")
            failed.append((country, team, lat, lon, str(exc)))
            continue

        elevation = data.get("elevation")
        rows = extract_rows(data, target_set, target_hours)
        write_csv(out_path, rows, elevation)
        if len(rows) != len(target_set):
            print(
                f"  Warning: wrote {len(rows)} rows, expected {len(target_set)} for {country} / {team}. "
                "Check the requested date range and the API response."
            )

        time.sleep(args.sleep + random.uniform(1, 10) + 1)

    if failed:
        failed_path = os.path.join(args.output, "failed_requests.csv")
        with open(failed_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Country", "Team", "Lat", "Lon", "Error"])
            writer.writerows(failed)
        print(f"Done with failures. See {failed_path}")
    else:
        print("Done.")


if __name__ == "__main__":
    main()
