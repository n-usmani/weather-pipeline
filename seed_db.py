import json
# Synthetic historical readings (for assessment only). Each row matches the
# shape produced by fetch_weather(). London's history is deliberately far from
# a realistic current reading so it should flag as anomalous.
seed = []
def add(city, temps):
    for i, t in enumerate(temps):
        seed.append({"city": city, "temperature": t, "timestamp": f"2026-07-2{i}T06:30"})
add("New York", [16.0, 17.0, 18.0, 19.0, 17.5])   # varied, current ~17 -> normal
add("London",   [10.0, 11.0, 9.0, 10.5, 11.5])    # cold cluster, current ~18 -> anomalous
add("Tokyo",    [30.0, 31.0, 29.0, 30.5, 30.2])   # varied, current ~30 -> normal
json.dump(seed, open("weather_db.json", "w"), indent=2)
print(f"Seeded {len(seed)} rows across 3 cities.")
