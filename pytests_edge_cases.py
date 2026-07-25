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


def make_fake_date(year, month, day):
    """A date subclass whose today() is fixed; all real date math still works."""
    class _FakeDate(wp.date):
        @classmethod
        def today(cls):
            return cls(year, month, day)
    return _FakeDate


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


# --- _get_json (hardening / documentation) -----------------------------------

def test_get_json_returns_bare_list(monkeypatch):
    # json.loads happily parses a top-level array. _get_json returns it as-is,
    # so a non-dict CAN escape to callers -- which is exactly why fetch_weather /
    # _fetch_history_year now isinstance-guard the payload.
    set_urlopen(monkeypatch, body=b"[1, 2, 3]")
    assert wp._get_json("http://x") == [1, 2, 3]


def test_get_json_returns_none_for_json_null(monkeypatch):
    # A bare `null` body parses to Python None -- another non-dict a naive
    # caller would blow up on.
    set_urlopen(monkeypatch, body=b"null")
    assert wp._get_json("http://x") is None


def test_get_json_non_utf8_body(monkeypatch):
    # Undecodable bytes raise UnicodeDecodeError, a ValueError subclass, so the
    # existing handler should catch it and re-raise a clean "bad response".
    set_urlopen(monkeypatch, body=b"\xff\xfe\xff")
    with pytest.raises(ValueError) as info:
        wp._get_json("http://x")
    assert "bad response" in str(info.value)


# --- _fetch_history_year (hardening / documentation) -------------------------

def test_fetch_history_year_unequal_lengths_truncate(monkeypatch):
    # zip() pairs by index and stops at the shorter list: the extra timestamp
    # "c" is silently dropped. Documents that behaviour so a future change that
    # needs strict alignment has a tripwire.
    payload = {"daily": {"time": ["a", "b", "c"],
                         "temperature_2m_mean": [10, 11]}}
    monkeypatch.setattr(wp, "_get_json", lambda url: payload)
    rows = wp._fetch_history_year(0, 0, wp.date(2019, 6, 15))
    assert rows == [
        {"temperature": 10.0, "timestamp": "a"},
        {"temperature": 11.0, "timestamp": "b"},
    ]


def test_fetch_history_year_drops_nan(monkeypatch):
    # NaN must be filtered like a null so it never poisons downstream stats.
    payload = {"daily": {"time": ["a", "b", "c"],
                         "temperature_2m_mean": [10, float("nan"), 12]}}
    monkeypatch.setattr(wp, "_get_json", lambda url: payload)
    rows = wp._fetch_history_year(0, 0, wp.date(2019, 6, 15))
    assert rows == [
        {"temperature": 10.0, "timestamp": "a"},
        {"temperature": 12.0, "timestamp": "c"},
    ]


# --- init_db (hardening / documentation) -------------------------------------

def test_init_db_jan_1_anchors_stay_jan_1(monkeypatch):
    # Anchoring on Jan 1 must not malform when the +/- window later crosses back
    # into the previous December (that subtraction happens inside
    # _fetch_history_year). The anchors themselves stay on Jan 1 of past years.
    monkeypatch.setattr(wp, "date", make_fake_date(2021, 1, 1))
    anchors = []
    monkeypatch.setattr(
        wp, "_fetch_history_year",
        lambda lat, long, anchor: anchors.append(anchor) or [],
    )
    wp.init_db()
    assert anchors, "no anchors were produced"
    assert all(a.month == 1 and a.day == 1 for a in anchors)


def test_init_db_zero_history_years(monkeypatch):
    # HISTORY_YEARS = 0 -> the range loop never runs -> empty db, no fetch calls,
    # no crash.
    monkeypatch.setattr(wp, "date", make_fake_date(2020, 6, 15))
    monkeypatch.setattr(wp, "HISTORY_YEARS", 0)

    def should_not_call(*args, **kwargs):
        raise AssertionError("_fetch_history_year must not be called")

    monkeypatch.setattr(wp, "_fetch_history_year", should_not_call)
    wp.init_db()
    assert wp._db == []


def test_init_db_normal_date_never_triggers_feb29_fallback(monkeypatch):
    # For an ordinary today (June 15) the Feb-29 day-1 fallback must never fire:
    # every anchor keeps day == 15 (no silent off-by-one).
    monkeypatch.setattr(wp, "date", make_fake_date(2020, 6, 15))
    anchors = []
    monkeypatch.setattr(
        wp, "_fetch_history_year",
        lambda lat, long, anchor: anchors.append(anchor) or [],
    )
    wp.init_db()
    assert anchors
    assert all(a.month == 6 and a.day == 15 for a in anchors)


# --- insert_readings (hardening / documentation) -----------------------------

def test_insert_readings_error_key_wins_over_valid_data():
    # Even with a perfectly good temperature, the presence of "error" must
    # veto the insert.
    wp.insert_readings({"city": "X", "temperature": 5.0, "error": "boom"})
    assert wp._db == []


def test_insert_readings_row_without_city_is_orphaned():
    # A row missing "city" is still inserted (no error key), but query_history
    # can never retrieve it -- documents that ingest does not enforce a city.
    wp.insert_readings({"temperature": 5.0, "timestamp": "t"})
    assert len(wp._db) == 1
    assert wp.query_history("New York") == []


def test_insert_readings_accepts_non_dict_argument():
    # Loose contract: `"error" in reading` is a membership test that a list
    # passes without being a real reading, so junk is appended rather than
    # rejected. Documents the gap in case we later want to tighten it.
    wp.insert_readings(["not", "a", "reading"])
    assert wp._db == [["not", "a", "reading"]]


# --- query_history (hardening / documentation) -------------------------------

def test_query_history_none_city_matches_none_query():
    # A row whose city is None is matched by querying None (both sides .get()
    # to None). Documents that None is a real, matchable key here.
    wp._db.append({"city": None, "temperature": 1.0})
    assert wp.query_history(None) == [{"city": None, "temperature": 1.0}]


def test_query_history_returns_live_references():
    # query_history returns the actual db rows, not copies: mutating a returned
    # row mutates the database. A tripwire before anyone relies on isolation.
    wp._db.append({"city": "A", "temperature": 1.0})
    rows = wp.query_history("A")
    rows[0]["temperature"] = 999.0
    assert wp._db[0]["temperature"] == 999.0


def test_query_history_is_case_sensitive():
    # "london" != "London": an exact match miss returns nothing.
    wp._db.append({"city": "London", "temperature": 1.0})
    assert wp.query_history("london") == []
