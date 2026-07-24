# SPEC: Weather Pipeline

## What It Does
Pulls data from Open-Meteo's API about three pre-decided cities, and given a city's current reading and historical readings, compute how far the city's weather is from the historical average.

## Inputs / Outputs
- Input: Pulled data from Open-Meteo API
- Output: A single JSON file containing relevant 

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
    # function to initialize the tabular database in SQLite
    # the database is a list of dicts
    # runs once, prior to execution of main loop

def insert_readings():
    # Runs once per API pull per city
    # Inserts the readings outputted by fetch_weather() as a new row in the database (each row is a dict)

def query_history(city: str) -> list[dict]:
    # Returns all the rows of data from the database for the given city

def compute_anomaly(todays_temp: dict, past_readings: list[dict]) -> float:
    # if no history, return "insufficient data"
    # calculate mean of past readings
    # if stddev of history = 0, return "insufficient variance to assess"
    # calculate z_score as deviation / stddev
    # anomalous if z_score >= 2

def report():
    for each city:
        fetch current reading
        pull past history from database (if no history, just treat z-score = null)
        z = compute_anomaly(current, history)
        print city, current_temp, z-score, flag if anomalous
