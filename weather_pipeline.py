"""Weather Pipeline (backward-accumulating).

A tool that, at time of run, compares each of three pre-decided cities' CURRENT
weather against that city's real historical weather, and flags anomalies
(readings >= 2 standard deviations from the historical mean).

Design (per SPEC.md):
  * History is backfilled up front from Open-Meteo's Archive API (ERA5), so a
    single run already has meaningful history -- no waiting, no seeding.
  * Comparison window is like-with-like across years: today's calendar date
    +/- WINDOW_DAYS for each of the last HISTORY_YEARS years (~70 readings).
    This answers "is today unusual for THIS TIME OF YEAR?" and avoids the
    seasonality confound by construction.
  * Like granularity: today's value and every history value are daily means
    (temperature_2m_mean, degrees C) -- not an instantaneous reading vs. means.
  * Only the Open-Meteo API is used; only the Python standard library is needed.
  * Never crashes on missing/faulty data or API failures.
"""

import json
import math
import statistics
import urllib.error
import urllib.request
from datetime import date, timedelta

# --- Configuration -----------------------------------------------------------

CITIES = [
    {"city": "New York", "lat": 40.7128, "long": -74.0060},
    {"city": "London", "lat": 51.5074, "long": -0.1278},
    {"city": "Tokyo", "lat": 35.6762, "long": 139.6503},
]

FORECAST_API = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_API = "https://archive-api.open-meteo.com/v1/archive"
REQUEST_TIMEOUT = 15  # seconds
OUTPUT_FILE = "weather_report.json"

WINDOW_DAYS = 3       # +/- this many days around today's calendar date
HISTORY_YEARS = 10    # how many past years to sample
ANOMALY_THRESHOLD = 2.0

# In-memory database: a list of dicts, each row = one daily-mean reading.
_db: list[dict] = []


# --- Helpers -----------------------------------------------------------------

def _get_json(url: str) -> dict:
    # Fetch + parse JSON, raising a plain ValueError on any failure so callers
    # can convert it to an error row without caring about the exception type.
    try:
        with urllib.request.urlopen(url, timeout=REQUEST_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise ValueError(f"HTTP {exc.code}: {exc.reason}")
    except urllib.error.URLError as exc:
        raise ValueError(f"network error: {exc.reason}")
    except (TimeoutError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"bad response: {exc}")


def _valid_temp(value):
    # Return `value` as a float only if it is a real, finite number; otherwise
    # None. This is the single gate for "is this a usable reading?" -- NaN,
    # None, booleans, and non-numeric junk all collapse to None ("no reading"),
    # so nothing fake is ever stored or fed to statistics.
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    return None


# --- Functions ---------------------------------------------------------------

def fetch_weather(city: str, lat: float, long: float) -> dict:
    # Today's "current" reading, as a daily mean (like-with-like vs. history).
    # 1. build a request URL using lat and long
    url = (
        f"{FORECAST_API}?latitude={lat}&longitude={long}"
        f"&daily=temperature_2m_mean&forecast_days=1&timezone=UTC"
    )
    # 2. retrieve the data from the API
    try:
        payload = _get_json(url)
    except ValueError as exc:
        # 3. if there's any error, return {"city": city, "error": ...}
        return {"city": city, "error": str(exc)}

    daily = payload.get("daily") if isinstance(payload, dict) else None
    if not isinstance(daily, dict):
        return {"city": city, "error": "missing temperature in response"}
    times = daily.get("time") or []
    temps = daily.get("temperature_2m_mean") or []
    temp = _valid_temp(temps[0]) if times and temps else None
    if temp is None:
        return {"city": city, "error": "missing temperature in response"}

    # 4. otherwise, extract temperature, timestamp -> return as dict
    return {"city": city, "temperature": temp, "timestamp": times[0]}


def _fetch_history_year(lat: float, long: float, anchor: date) -> list[dict]:
    # Fetch daily means for anchor +/- WINDOW_DAYS from the Archive API.
    start = anchor - timedelta(days=WINDOW_DAYS)
    end = anchor + timedelta(days=WINDOW_DAYS)
    url = (
        f"{ARCHIVE_API}?latitude={lat}&longitude={long}"
        f"&start_date={start.isoformat()}&end_date={end.isoformat()}"
        f"&daily=temperature_2m_mean&timezone=UTC"
    )
    payload = _get_json(url)  # may raise ValueError; caller handles
    daily = payload.get("daily") if isinstance(payload, dict) else None
    if not isinstance(daily, dict):
        return []
    times = daily.get("time") or []
    temps = daily.get("temperature_2m_mean") or []
    rows = []
    for t, temp in zip(times, temps):
        temp = _valid_temp(temp)
        if temp is not None:  # skip only the bad row, keep the rest of the year
            rows.append({"temperature": temp, "timestamp": t})
    return rows


def init_db() -> None:
    # Initialize the database (a list of dicts) and backfill history. Runs once,
    # prior to the report loop. For each city, pull the same calendar window
    # across the last HISTORY_YEARS years from the Archive API into the db.
    global _db
    _db = []
    today = date.today()
    for entry in CITIES:
        for year_offset in range(1, HISTORY_YEARS + 1):
            year = today.year - year_offset
            # Anchor the window on the same month/day in a past year.
            # (Feb 29 -> fall back to Feb 28 for non-leap years.)
            try:
                anchor = today.replace(year=year)
            except ValueError:
                anchor = today.replace(year=year, day=today.day - 1)
            try:
                rows = _fetch_history_year(entry["lat"], entry["long"], anchor)
            except ValueError:
                # Skip a year we couldn't fetch; never crash on API failure.
                continue
            for row in rows:
                insert_readings({"city": entry["city"], **row})


def insert_readings(reading: dict) -> None:
    # If fetch_weather() (or a history fetch) yielded an error, don't insert.
    if not reading or "error" in reading:
        return
    # Otherwise, insert the reading as a new row in the database.
    _db.append(reading)


def query_history(city: str) -> list[dict]:
    # Return all rows of data from the database for the given city.
    return [row for row in _db if row.get("city") == city]


def compute_anomaly(todays_temp: dict, past_readings: list[dict]):
    # if no history, return "insufficient data"
    temps = [
        v for v in (_valid_temp(row.get("temperature")) for row in past_readings)
        if v is not None
    ]
    if len(temps) < 2:
        return "insufficient data"

    # today's reading must itself be a real number to compare against
    today = _valid_temp(todays_temp.get("temperature")) if isinstance(todays_temp, dict) else None
    if today is None:
        return "insufficient data"

    # calculate mean of past readings
    mean = statistics.mean(temps)
    stddev = statistics.stdev(temps)

    # if stddev of history = 0, return "insufficient variance to assess"
    if stddev == 0:
        return "insufficient variance to assess"

    # calculate z_score as deviation / stddev
    return (today - mean) / stddev


def report() -> None:
    results = []
    for entry in CITIES:
        city = entry["city"]

        # Pull past history from the database.
        history = query_history(city)

        # Fetch today's reading.
        current = fetch_weather(city, entry["lat"], entry["long"])

        if "error" in current:
            results.append({
                "city": city,
                "current_temp": None,
                "z_score": None,
                "anomalous": None,
                "error": current["error"],
            })
            continue

        z = compute_anomaly(current, history)

        # None (JSON null) is our contract for "no usable reading" -- never a
        # fake number. Same validation gate as compute_anomaly, so the reported
        # count/mean can't disagree with what the z-score was computed from.
        current_temp = _valid_temp(current.get("temperature"))
        temps = [
            v for v in (_valid_temp(r.get("temperature")) for r in history)
            if v is not None
        ]
        if isinstance(z, (int, float)):
            z_value = round(z, 3)
            anomalous = abs(z) >= ANOMALY_THRESHOLD
            note = None
        else:
            z_value = None
            anomalous = None
            note = z

        results.append({
            "city": city,
            "date": current["timestamp"],
            "current_temp": current_temp,
            "history_count": len(temps),
            "history_mean": round(statistics.mean(temps), 2) if temps else None,
            "z_score": z_value,
            "anomalous": anomalous,
            "note": note,
        })

    output = {
        "description": (
            "Today's daily-mean temperature vs. the same calendar window "
            f"(+/-{WINDOW_DAYS} days) across the last {HISTORY_YEARS} years."
        ),
        "anomaly_threshold_stddevs": ANOMALY_THRESHOLD,
        "results": results,
    }
    try:
        with open(OUTPUT_FILE, "w", encoding="utf-8") as handle:
            # allow_nan=False -> emit strictly valid JSON; if a non-finite value
            # ever slips past the gates above it raises here rather than writing
            # the invalid bare token NaN/Infinity.
            json.dump(output, handle, indent=2, allow_nan=False)
        print(f"Report written to {OUTPUT_FILE}")
    except (OSError, ValueError) as exc:
        print(f"Failed to write report: {exc}")


if __name__ == "__main__":
    init_db()
    report()
