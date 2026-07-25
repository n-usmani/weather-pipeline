"""Pytest suite for weather_pipeline.py.

Network-touching code is exercised by monkeypatching the module's own seams
(``urlopen`` / ``_get_json`` / ``_fetch_history_year`` / ``fetch_weather``) so no
real HTTP request is ever made. The module-level in-memory ``_db`` is reset
before every test by the ``fresh_db`` fixture.
"""

import json
import urllib.error

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


class FakeResp:
    """Minimal stand-in for the urlopen response context manager."""

    def __init__(self, data: bytes):
        self._data = data

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def set_urlopen(monkeypatch, *, body=None, exc=None):
    """Patch wp.urllib.request.urlopen to return `body` bytes or raise `exc`."""
    def fake_urlopen(url, timeout=None):
        if exc is not None:
            raise exc
        return FakeResp(body)
    monkeypatch.setattr(wp.urllib.request, "urlopen", fake_urlopen)


class FakeDate(wp.date):
    """date subclass with a fixed today(); all real date math still works."""
    _today = wp.date(2020, 6, 15)

    @classmethod
    def today(cls):
        return cls._today


# --- _get_json ---------------------------------------------------------------

def test_get_json_returns_parsed_dict(monkeypatch):
    set_urlopen(monkeypatch, body=b'{"a": 1, "b": [2, 3]}')
    assert wp._get_json("http://x") == {"a": 1, "b": [2, 3]}


def test_get_json_http_error(monkeypatch):
    err = urllib.error.HTTPError("http://x", 503, "Service Unavailable", {}, None)
    set_urlopen(monkeypatch, exc=err)
    with pytest.raises(ValueError) as info:
        wp._get_json("http://x")
    assert "HTTP 503" in str(info.value)


def test_get_json_url_error(monkeypatch):
    set_urlopen(monkeypatch, exc=urllib.error.URLError("no route to host"))
    with pytest.raises(ValueError) as info:
        wp._get_json("http://x")
    assert "network error" in str(info.value)


def test_get_json_malformed_body(monkeypatch):
    set_urlopen(monkeypatch, body=b"this is not json")
    with pytest.raises(ValueError) as info:
        wp._get_json("http://x")
    assert "bad response" in str(info.value)


def test_get_json_timeout(monkeypatch):
    set_urlopen(monkeypatch, exc=TimeoutError("timed out"))
    with pytest.raises(ValueError) as info:
        wp._get_json("http://x")
    assert "bad response" in str(info.value)


# --- fetch_weather -----------------------------------------------------------

def test_fetch_weather_happy_path(monkeypatch):
    payload = {"daily": {"time": ["2020-06-15"], "temperature_2m_mean": [21]}}
    monkeypatch.setattr(wp, "_get_json", lambda url: payload)
    result = wp.fetch_weather("New York", 40.7, -74.0)
    assert result == {"city": "New York", "temperature": 21.0, "timestamp": "2020-06-15"}
    assert isinstance(result["temperature"], float)  # int coerced to float


def test_fetch_weather_api_error_returns_error_dict(monkeypatch):
    def boom(url):
        raise ValueError("HTTP 500: Server Error")
    monkeypatch.setattr(wp, "_get_json", boom)
    result = wp.fetch_weather("London", 51.5, -0.1)
    assert result == {"city": "London", "error": "HTTP 500: Server Error"}


def test_fetch_weather_missing_daily_key(monkeypatch):
    monkeypatch.setattr(wp, "_get_json", lambda url: {"foo": "bar"})
    result = wp.fetch_weather("Tokyo", 35.6, 139.6)
    assert result == {"city": "Tokyo", "error": "missing temperature in response"}


def test_fetch_weather_null_temperature(monkeypatch):
    payload = {"daily": {"time": ["2020-06-15"], "temperature_2m_mean": [None]}}
    monkeypatch.setattr(wp, "_get_json", lambda url: payload)
    result = wp.fetch_weather("Tokyo", 35.6, 139.6)
    assert result == {"city": "Tokyo", "error": "missing temperature in response"}


def test_fetch_weather_empty_temps_list(monkeypatch):
    payload = {"daily": {"time": [], "temperature_2m_mean": []}}
    monkeypatch.setattr(wp, "_get_json", lambda url: payload)
    assert "error" in wp.fetch_weather("Tokyo", 35.6, 139.6)


# --- _fetch_history_year -----------------------------------------------------

def test_fetch_history_year_aligned_rows(monkeypatch):
    payload = {"daily": {"time": ["2019-06-14", "2019-06-15"],
                         "temperature_2m_mean": [18, 19]}}
    monkeypatch.setattr(wp, "_get_json", lambda url: payload)
    rows = wp._fetch_history_year(40.7, -74.0, wp.date(2019, 6, 15))
    assert rows == [
        {"temperature": 18.0, "timestamp": "2019-06-14"},
        {"temperature": 19.0, "timestamp": "2019-06-15"},
    ]
    assert all(isinstance(r["temperature"], float) for r in rows)


def test_fetch_history_year_drops_nulls(monkeypatch):
    payload = {"daily": {"time": ["a", "b", "c"],
                         "temperature_2m_mean": [10, None, 12]}}
    monkeypatch.setattr(wp, "_get_json", lambda url: payload)
    rows = wp._fetch_history_year(0, 0, wp.date(2019, 6, 15))
    assert rows == [
        {"temperature": 10.0, "timestamp": "a"},
        {"temperature": 12.0, "timestamp": "c"},
    ]


def test_fetch_history_year_empty_payload(monkeypatch):
    monkeypatch.setattr(wp, "_get_json", lambda url: {})
    assert wp._fetch_history_year(0, 0, wp.date(2019, 6, 15)) == []


def test_fetch_history_year_propagates_valueerror(monkeypatch):
    def boom(url):
        raise ValueError("network error: down")
    monkeypatch.setattr(wp, "_get_json", boom)
    with pytest.raises(ValueError):
        wp._fetch_history_year(0, 0, wp.date(2019, 6, 15))


def test_fetch_history_year_url_has_window_bounds(monkeypatch):
    captured = {}

    def capture(url):
        captured["url"] = url
        return {"daily": {"time": [], "temperature_2m_mean": []}}

    monkeypatch.setattr(wp, "_get_json", capture)
    wp._fetch_history_year(0, 0, wp.date(2019, 6, 15))
    # anchor 2019-06-15 +/- WINDOW_DAYS(3) -> 06-12 .. 06-18
    assert "start_date=2019-06-12" in captured["url"]
    assert "end_date=2019-06-18" in captured["url"]


# --- init_db -----------------------------------------------------------------

def test_init_db_populates_all_cities(monkeypatch):
    monkeypatch.setattr(wp, "date", FakeDate)
    monkeypatch.setattr(
        wp, "_fetch_history_year",
        lambda lat, long, anchor: [{"temperature": 20.0, "timestamp": anchor.isoformat()}],
    )
    wp.init_db()
    cities = {row["city"] for row in wp._db}
    assert cities == {c["city"] for c in wp.CITIES}
    # one row per city per year sampled
    assert len(wp._db) == len(wp.CITIES) * wp.HISTORY_YEARS


def test_init_db_skips_failing_years(monkeypatch):
    monkeypatch.setattr(wp, "date", FakeDate)
    calls = {"n": 0}

    def flaky(lat, long, anchor):
        calls["n"] += 1
        if calls["n"] % 2 == 0:  # every other fetch fails
            raise ValueError("boom")
        return [{"temperature": 20.0, "timestamp": anchor.isoformat()}]

    monkeypatch.setattr(wp, "_fetch_history_year", flaky)
    wp.init_db()  # must not raise
    assert 0 < len(wp._db) < len(wp.CITIES) * wp.HISTORY_YEARS


def test_init_db_all_failures_leaves_empty_db(monkeypatch):
    monkeypatch.setattr(wp, "date", FakeDate)

    def always_fail(lat, long, anchor):
        raise ValueError("down")

    monkeypatch.setattr(wp, "_fetch_history_year", always_fail)
    wp.init_db()
    assert wp._db == []


def test_init_db_resets_on_each_call(monkeypatch):
    monkeypatch.setattr(wp, "date", FakeDate)
    monkeypatch.setattr(
        wp, "_fetch_history_year",
        lambda lat, long, anchor: [{"temperature": 1.0, "timestamp": "t"}],
    )
    wp.init_db()
    first = len(wp._db)
    wp.init_db()
    assert len(wp._db) == first  # no accumulation across runs


def test_init_db_feb29_fallback(monkeypatch):
    class LeapDate(wp.date):
        @classmethod
        def today(cls):
            return cls(2020, 2, 29)  # 2020 is a leap year

    monkeypatch.setattr(wp, "date", LeapDate)
    anchors = []

    def record(lat, long, anchor):
        anchors.append(anchor)
        return []

    monkeypatch.setattr(wp, "_fetch_history_year", record)
    wp.init_db()  # must not raise on non-leap past years
    # non-leap years fall back to Feb 28
    assert all(a.month == 2 and a.day in (28, 29) for a in anchors)
    assert any(a.day == 28 for a in anchors)


# --- insert_readings ---------------------------------------------------------

def test_insert_readings_valid_row():
    wp.insert_readings({"city": "X", "temperature": 5.0, "timestamp": "t"})
    assert wp._db == [{"city": "X", "temperature": 5.0, "timestamp": "t"}]


def test_insert_readings_skips_error_dict():
    wp.insert_readings({"city": "X", "error": "boom"})
    assert wp._db == []


def test_insert_readings_skips_empty():
    wp.insert_readings({})
    wp.insert_readings(None)
    assert wp._db == []


def test_insert_readings_appends_in_order():
    wp.insert_readings({"city": "A", "temperature": 1.0})
    wp.insert_readings({"city": "B", "temperature": 2.0})
    assert [r["city"] for r in wp._db] == ["A", "B"]


# --- query_history -----------------------------------------------------------

def test_query_history_filters_by_city():
    wp._db.extend([
        {"city": "A", "temperature": 1.0},
        {"city": "B", "temperature": 2.0},
        {"city": "A", "temperature": 3.0},
    ])
    rows = wp.query_history("A")
    assert [r["temperature"] for r in rows] == [1.0, 3.0]


def test_query_history_unknown_city():
    wp._db.append({"city": "A", "temperature": 1.0})
    assert wp.query_history("Z") == []


def test_query_history_empty_db():
    assert wp.query_history("A") == []


def test_query_history_ignores_rows_without_city():
    wp._db.extend([{"temperature": 1.0}, {"city": "A", "temperature": 2.0}])
    rows = wp.query_history("A")  # must not raise KeyError
    assert rows == [{"city": "A", "temperature": 2.0}]


# --- compute_anomaly ---------------------------------------------------------

def test_compute_anomaly_insufficient_data():
    assert wp.compute_anomaly({"temperature": 20.0}, []) == "insufficient data"
    one = [{"temperature": 10.0}]
    assert wp.compute_anomaly({"temperature": 20.0}, one) == "insufficient data"


def test_compute_anomaly_zero_variance():
    history = [{"temperature": 10.0}, {"temperature": 10.0}, {"temperature": 10.0}]
    assert wp.compute_anomaly({"temperature": 20.0}, history) == \
        "insufficient variance to assess"


def test_compute_anomaly_normal_reading():
    history = [{"temperature": t} for t in (16.0, 17.0, 18.0, 19.0, 17.5)]
    z = wp.compute_anomaly({"temperature": 17.0}, history)
    assert isinstance(z, float)
    assert abs(z) < wp.ANOMALY_THRESHOLD


def test_compute_anomaly_flags_and_sign():
    history = [{"temperature": t} for t in (10.0, 11.0, 9.0, 10.5, 11.5)]
    z = wp.compute_anomaly({"temperature": 25.0}, history)
    assert isinstance(z, float)
    assert z > 0 and abs(z) >= wp.ANOMALY_THRESHOLD  # far above mean -> anomalous


def test_compute_anomaly_ignores_non_numeric_history():
    history = [
        {"temperature": 10.0},
        {"temperature": None},
        {"temperature": "oops"},
        {"temperature": 12.0},
    ]
    # only the two numeric readings count -> valid float z-score
    assert isinstance(wp.compute_anomaly({"temperature": 11.0}, history), float)


# --- report ------------------------------------------------------------------

def read_report(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def test_report_happy_path(monkeypatch, tmp_path):
    out = tmp_path / "report.json"
    monkeypatch.setattr(wp, "OUTPUT_FILE", str(out))
    wp._db.extend(
        {"city": "New York", "temperature": t} for t in (16.0, 17.0, 18.0, 19.0, 17.5)
    )
    monkeypatch.setattr(wp, "CITIES", [{"city": "New York", "lat": 0, "long": 0}])
    monkeypatch.setattr(
        wp, "fetch_weather",
        lambda city, lat, long: {"city": city, "temperature": 17.0, "timestamp": "2020-06-15"},
    )
    wp.report()
    data = read_report(out)
    assert data["results"][0]["city"] == "New York"
    for key in ("current_temp", "z_score", "anomalous", "history_count"):
        assert key in data["results"][0]
    assert data["results"][0]["anomalous"] is False


def test_report_fetch_error(monkeypatch, tmp_path):
    out = tmp_path / "report.json"
    monkeypatch.setattr(wp, "OUTPUT_FILE", str(out))
    monkeypatch.setattr(wp, "CITIES", [{"city": "London", "lat": 0, "long": 0}])
    monkeypatch.setattr(
        wp, "fetch_weather",
        lambda city, lat, long: {"city": city, "error": "network error: down"},
    )
    wp.report()
    row = read_report(out)["results"][0]
    assert row["error"] == "network error: down"
    assert row["z_score"] is None and row["anomalous"] is None


def test_report_insufficient_history(monkeypatch, tmp_path):
    out = tmp_path / "report.json"
    monkeypatch.setattr(wp, "OUTPUT_FILE", str(out))
    monkeypatch.setattr(wp, "CITIES", [{"city": "Tokyo", "lat": 0, "long": 0}])
    # empty history -> compute_anomaly returns a string note
    monkeypatch.setattr(
        wp, "fetch_weather",
        lambda city, lat, long: {"city": city, "temperature": 30.0, "timestamp": "2020-06-15"},
    )
    wp.report()
    row = read_report(out)["results"][0]
    assert row["z_score"] is None
    assert row["anomalous"] is None
    assert row["note"] == "insufficient data"


def test_report_anomalous_city(monkeypatch, tmp_path):
    out = tmp_path / "report.json"
    monkeypatch.setattr(wp, "OUTPUT_FILE", str(out))
    monkeypatch.setattr(wp, "CITIES", [{"city": "London", "lat": 0, "long": 0}])
    wp._db.extend(
        {"city": "London", "temperature": t} for t in (10.0, 11.0, 9.0, 10.5, 11.5)
    )
    monkeypatch.setattr(
        wp, "fetch_weather",
        lambda city, lat, long: {"city": city, "temperature": 25.0, "timestamp": "2020-06-15"},
    )
    wp.report()
    row = read_report(out)["results"][0]
    assert row["anomalous"] is True
    assert isinstance(row["z_score"], float)
    # z_score is rounded to 3 decimal places
    assert round(row["z_score"], 3) == row["z_score"]


def test_report_handles_write_failure(monkeypatch, capsys):
    monkeypatch.setattr(wp, "CITIES", [{"city": "Tokyo", "lat": 0, "long": 0}])
    monkeypatch.setattr(
        wp, "fetch_weather",
        lambda city, lat, long: {"city": city, "temperature": 30.0, "timestamp": "t"},
    )

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("builtins.open", boom)
    wp.report()  # must not raise
    assert "Failed to write report" in capsys.readouterr().out
