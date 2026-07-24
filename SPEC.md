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

## Function Headers
def fetch_weather(city: str, lat: float, long: float) -> dict:
    # 1. build a request URL using lat and long
    # 2. retrieve the data from the API
    # 3. if there's any error, return {"city": city, "error": }
    # 4. otherwise, extract temperature, timestamp -> return as dict

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
