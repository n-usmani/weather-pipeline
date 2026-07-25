# SPEC: Weather Pipeline

## What It Does
Pulls data from Open-Meteo's API about three pre-decided cities, and given a city's current reading and historical readings, compute how far the city's weather is from the historical average.

- NEW DIRECTION (backward-accumulating): rather than building history forward one run at a time, treat this as a tool that, at time of run, compares each city's CURRENT weather against that city's real historical weather.
- History is backfilled up front from Open-Meteo's free Archive API (ERA5 reanalysis) — so a single run already has meaningful history; no waiting or seeding.
- Comparison window (CHOSEN — like-with-like across years): history = the same calendar window across many past years — today's date ±N days (e.g. ±3) for each of the last ~10 years, i.e. ~70 daily readings. This answers "is today unusual FOR THIS TIME OF YEAR?" and avoids the seasonality confound by construction (every history point is in-season).
  - Sourced from the Archive API (prior years), whose few-day lag is irrelevant since we never query the most recent days.
  - Compare like granularity: today's value and the historical values should both be daily means (or both daily max) — not an instantaneous current reading vs. daily means.
  - (Alternative, NOT chosen: trailing last-10-days window for "unusual vs. the past week-and-a-half.")

## Inputs / Outputs
- Input: Pulled data from Open-Meteo API
- Output: A single JSON file containing relevant data

## Constraints
- Must not crash if there's no available data / data is faulty
- Results must be printed into a JSON file in an organized, readable/useful format
- Handle API failures gracefully
- No external data sources besides the Open-Meteo API

## Data Contract (missing/faulty readings)
- A "no reading" is represented as `None` (JSON `null`) — NEVER a fake number or sentinel like NaN. (Rejected the NaN-as-sentinel approach: it doubled as both a real value and a missing marker, which broke statistics and produced invalid JSON.)
- A reading is "usable" only if it is a real, finite number (int/float, not NaN/Infinity, not bool, not a string). This single gate is applied at every ingest and consume point so no fake value is ever stored or fed to statistics.
- Non-numeric / NaN / null values are dropped per-row (a single bad row must not discard a whole year of history), not per-batch.
- Output JSON must always be strictly valid — no bare `NaN`/`Infinity` tokens (write with `allow_nan=False` as a last-line guard).

## Function Headers
def fetch_weather(city: str, lat: float, long: float) -> dict:
    # 1. build a request URL using lat and long
    # 2. retrieve the data from the API
    # 3. if there's any error, return {"city": city, "error": }
    # 4. otherwise, extract temperature, timestamp -> return as dict
    # - a malformed payload/daily shape or a non-finite/non-numeric temperature
    #   also yields an error dict (never crashes on faulty data)

def init_db():
    # function to initialize the tabular database as a list of dicts
    # runs once, prior to execution of main loop
    # backfill loop: for each city, pull the same calendar window across the last ~10 years
    #   (today's date +/- N days per year) from the Archive API into the db as history

def insert_readings():
    # Runs once per API pull per city
    # If fetch_weather() yielded an error, then don't insert
    # Otherwise, insert readings outputted by fetch_weather() as a new row in the database (each row is a dict)

def query_history(city: str) -> list[dict]:
    # Returns all the rows of data from the database for the given city

def compute_anomaly(todays_temp: dict, past_readings: list[dict]) -> float:
    # if no history, return "insufficient data"
    # only usable (finite numeric) history readings count; if fewer than 2, "insufficient data"
    # if today's own reading is missing/non-numeric, also "insufficient data" (no crash)
    # calculate mean of past readings
    # if stddev of history = 0, return "insufficient variance to assess"
    # calculate z_score as deviation / stddev
    # anomalous if |z_score| >= 2

def report():
    for each city:
        pull past history from database (if no history, just treat z-score = null)
        fetch current reading and call insert_readings() on i
        z = compute_anomaly(current, history)
        put city, current_temp, z-score, flag if anomalous into the JSON file as a new line
    # - current_temp and history are passed through the same usable-reading gate,
    #   so the reported count/mean can't disagree with what the z-score used
    # - a missing/non-numeric reading is reported as null, never a fake number
    # - the written JSON is always strictly valid (allow_nan=False)
