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
