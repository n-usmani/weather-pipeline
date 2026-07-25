"""Adversarial edge-case suite for weather_pipeline.py.

Unlike pytests_pipeline.py (which confirms the happy paths and the already-
handled failure modes), every test here targets a case I *expect* to expose a
real defect. Each assertion encodes the DESIRED behaviour, so a failure means
the code needs fixing -- not that the test is wrong.

The common thread: fetch_weather() coerces with a bare ``float()`` and report()
reads history with unguarded ``row["temperature"]``, whereas compute_anomaly()
defends itself with ``.get()`` and an ``isinstance`` filter. Those inconsistent
contracts are where things should break.

As in the sibling suite, no real HTTP is ever made (``_get_json`` is patched)
and the module-level ``_db`` is reset before each test.
"""

import math

import pytest

import weather_pipeline as wp


# --- Fixtures / helpers ------------------------------------------------------

@pytest.fixture(autouse=True)
def fresh_db():
    """Give every test an empty database and restore afterwards."""
    saved = wp._db
    wp._db = []
    yield
    wp._db = saved


def read_report(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


# --- fetch_weather -----------------------------------------------------------

def test_fetch_weather_non_numeric_temperature(monkeypatch):
    # A non-numeric temperature must not crash the pipeline; per SPEC the
    # function should return an error dict. Currently float("warm") raises an
    # uncaught ValueError.
    payload = {"daily": {"time": ["2020-06-15"], "temperature_2m_mean": ["warm"]}}
    monkeypatch.setattr(wp, "_get_json", lambda url: payload)
    result = wp.fetch_weather("New York", 40.7, -74.0)
    assert "error" in result, f"expected graceful error dict, got {result!r}"


def test_fetch_weather_daily_not_a_dict(monkeypatch):
    # If the API returns a malformed "daily" (a list instead of an object),
    # daily.get(...) raises AttributeError, which is not caught by the
    # ValueError handler. Should degrade to an error dict instead.
    monkeypatch.setattr(wp, "_get_json", lambda url: {"daily": [1, 2, 3]})
    result = wp.fetch_weather("London", 51.5, -0.1)
    assert "error" in result, f"expected graceful error dict, got {result!r}"


def test_fetch_weather_nan_temperature(monkeypatch):
    # NaN is not a usable reading. temps[0] is None is False, so it slips
    # through and produces a reading of nan rather than being treated as
    # missing data.
    payload = {"daily": {"time": ["2020-06-15"],
                         "temperature_2m_mean": [float("nan")]}}
    monkeypatch.setattr(wp, "_get_json", lambda url: payload)
    result = wp.fetch_weather("Tokyo", 35.6, 139.6)
    assert "error" in result or not math.isnan(result.get("temperature", 0.0)), \
        f"NaN leaked through as a valid reading: {result!r}"


# --- _fetch_history_year -----------------------------------------------------

def test_fetch_history_year_non_numeric_row_dropped(monkeypatch):
    # One bad value mid-list should drop only that row, keeping the good ones --
    # the same tolerance compute_anomaly shows. Currently float("x") raises,
    # discarding the ENTIRE year of history.
    payload = {"daily": {"time": ["a", "b", "c"],
                         "temperature_2m_mean": [10, "x", 12]}}
    monkeypatch.setattr(wp, "_get_json", lambda url: payload)
    rows = wp._fetch_history_year(0, 0, wp.date(2019, 6, 15))
    assert rows == [
        {"temperature": 10.0, "timestamp": "a"},
        {"temperature": 12.0, "timestamp": "c"},
    ], f"expected the good rows to survive, got {rows!r}"


# --- compute_anomaly ---------------------------------------------------------

def test_compute_anomaly_today_missing_temperature_key():
    # History access uses .get(); today's access uses todays_temp["temperature"].
    # A today-reading without the key should degrade gracefully, not KeyError.
    history = [{"temperature": t} for t in (16.0, 17.0, 18.0, 19.0)]
    result = wp.compute_anomaly({}, history)
    assert isinstance(result, (str, float)), \
        f"expected a note or z-score, got a crash-y {result!r}"


def test_compute_anomaly_today_temperature_none():
    # A None current temperature should not raise TypeError on None - mean.
    history = [{"temperature": t} for t in (16.0, 17.0, 18.0, 19.0)]
    result = wp.compute_anomaly({"temperature": None}, history)
    assert isinstance(result, (str, float)), \
        f"expected a note or z-score, got a crash-y {result!r}"


def test_compute_anomaly_nan_in_history_excluded():
    # A NaN reading passes the isinstance(float) filter and poisons mean/stdev,
    # yielding a NaN z-score. NaN should be filtered like other junk so a finite
    # z-score is returned from the two valid readings.
    history = [{"temperature": 10.0},
               {"temperature": float("nan")},
               {"temperature": 12.0}]
    result = wp.compute_anomaly({"temperature": 11.0}, history)
    assert isinstance(result, float) and math.isfinite(result), \
        f"NaN poisoned the z-score: {result!r}"


# --- report ------------------------------------------------------------------

def test_report_history_row_missing_temperature(monkeypatch, tmp_path):
    # report() line ~192 does r["temperature"] directly (no .get). A single
    # history row without the key crashes the whole run.
    out = tmp_path / "report.json"
    monkeypatch.setattr(wp, "OUTPUT_FILE", str(out))
    monkeypatch.setattr(wp, "CITIES", [{"city": "New York", "lat": 0, "long": 0}])
    wp._db.extend([
        {"city": "New York", "temperature": 16.0},
        {"city": "New York"},  # malformed row: no temperature
        {"city": "New York", "temperature": 18.0},
    ])
    monkeypatch.setattr(
        wp, "fetch_weather",
        lambda city, lat, long: {"city": city, "temperature": 17.0, "timestamp": "2020-06-15"},
    )
    wp.report()  # must not raise
    assert out.exists(), "report crashed on a malformed history row"


def test_report_history_row_non_numeric_temperature(monkeypatch, tmp_path):
    # Same line as above but the value is present and non-numeric:
    # statistics.mean() over a list containing a string raises TypeError,
    # even though compute_anomaly filtered it out.
    out = tmp_path / "report.json"
    monkeypatch.setattr(wp, "OUTPUT_FILE", str(out))
    monkeypatch.setattr(wp, "CITIES", [{"city": "London", "lat": 0, "long": 0}])
    wp._db.extend([
        {"city": "London", "temperature": 10.0},
        {"city": "London", "temperature": "oops"},
        {"city": "London", "temperature": 12.0},
    ])
    monkeypatch.setattr(
        wp, "fetch_weather",
        lambda city, lat, long: {"city": city, "temperature": 11.0, "timestamp": "2020-06-15"},
    )
    wp.report()  # must not raise
    assert out.exists(), "report crashed on a non-numeric history row"


def test_report_output_contains_no_nan_token(monkeypatch, tmp_path):
    # Python's json.dump happily writes the bare token NaN, which is invalid
    # JSON that strict parsers (and most other languages) reject. The report
    # must never contain it, even when the current reading is NaN.
    out = tmp_path / "report.json"
    monkeypatch.setattr(wp, "OUTPUT_FILE", str(out))
    monkeypatch.setattr(wp, "CITIES", [{"city": "Tokyo", "lat": 0, "long": 0}])
    wp._db.extend(
        {"city": "Tokyo", "temperature": t} for t in (29.0, 30.0, 31.0)
    )
    monkeypatch.setattr(
        wp, "fetch_weather",
        lambda city, lat, long: {"city": city, "temperature": float("nan"), "timestamp": "2020-06-15"},
    )
    wp.report()
    assert "NaN" not in read_report(out), "report wrote invalid JSON (NaN token)"
