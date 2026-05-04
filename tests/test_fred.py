"""
Tests for src/ingestion/fred.py — FRED time-series ingestion.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Callable, Optional

import httpx
import pytest

from ingestion.fred import (
    DEFAULT_BASE_URL,
    FRED_SERIES,
    FREDOutcome,
    SeriesConfig,
    _build_url,
    _parse_value,
    fetch_all,
    fetch_all_from_env,
    fetch_series,
    get_latest_observation,
)
from storage.db import get_connection, init_db

API_KEY = "test_fred_key_xyz123"

S = SeriesConfig(
    series_id="DGS10",
    label="10-year Treasury yield",
    units="%",
)


# =============================================================================
# Fake HTTP layer
# =============================================================================

class FakeJSONResponse:
    def __init__(
        self,
        status_code: int,
        payload: Any = None,
        broken_body: Optional[str] = None,
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self._broken_body = broken_body
        self.headers: dict[str, str] = {}

    def json(self) -> Any:
        if self._broken_body is not None:
            raise json.JSONDecodeError("simulated", self._broken_body, 0)
        return self._payload


class FakeHTTPClient:
    def __init__(
        self,
        responder: "FakeJSONResponse | Callable[[str, dict[str, str]], Any]",
    ) -> None:
        self._responder = responder
        self.calls: list[tuple[str, dict[str, str]]] = []

    def get(self, url: str, headers: Optional[dict[str, str]] = None) -> FakeJSONResponse:
        self.calls.append((url, dict(headers or {})))
        if callable(self._responder):
            r = self._responder(url, headers or {})
            if isinstance(r, Exception):
                raise r
            return r
        return self._responder

    def close(self) -> None: ...
    def __enter__(self) -> "FakeHTTPClient": return self
    def __exit__(self, *a: Any) -> bool: return False


# =============================================================================
# Canned payloads (FRED /series/observations response shape)
# =============================================================================

OBSERVATIONS_OK = {
    "realtime_start": "2026-04-30",
    "realtime_end": "2026-04-30",
    "units": "lin",
    "count": 1234,
    "observations": [
        {"realtime_start": "2026-04-30", "realtime_end": "2026-04-30",
         "date": "2026-04-30", "value": "4.21"},
        {"realtime_start": "2026-04-30", "realtime_end": "2026-04-30",
         "date": "2026-04-29", "value": "4.18"},
        {"realtime_start": "2026-04-30", "realtime_end": "2026-04-30",
         "date": "2026-04-28", "value": "."},  # FRED's missing-value sentinel
        {"realtime_start": "2026-04-30", "realtime_end": "2026-04-30",
         "date": "2026-04-27", "value": "4.15"},
    ],
}


# =============================================================================
# _parse_value
# =============================================================================

def test_parse_value_handles_normal_float() -> None:
    assert _parse_value("4.21") == 4.21
    assert _parse_value("0") == 0.0
    assert _parse_value("-0.5") == -0.5


def test_parse_value_treats_dot_as_missing() -> None:
    assert _parse_value(".") is None


def test_parse_value_treats_empty_as_missing() -> None:
    assert _parse_value("") is None
    assert _parse_value(None) is None


def test_parse_value_treats_whitespace_as_missing() -> None:
    assert _parse_value("   ") is None


def test_parse_value_handles_garbage() -> None:
    assert _parse_value("not a number") is None


# =============================================================================
# URL construction (auth contract)
# =============================================================================

def test_build_url_includes_required_params() -> None:
    url = _build_url("DGS10", API_KEY, limit=10, base_url=DEFAULT_BASE_URL)
    assert url.startswith(DEFAULT_BASE_URL + "/series/observations?")
    assert "series_id=DGS10" in url
    assert f"api_key={API_KEY}" in url
    assert "file_type=json" in url
    assert "sort_order=desc" in url
    assert "limit=10" in url


# =============================================================================
# fetch_series — happy path + persistence
# =============================================================================

def test_fetch_series_writes_observations() -> None:
    client = FakeHTTPClient(FakeJSONResponse(200, payload=OBSERVATIONS_OK))
    with get_connection(":memory:") as conn:
        init_db(conn)
        outcome = fetch_series(S, conn, api_key=API_KEY, client=client)

    assert outcome.status == 200
    assert outcome.rows_written == 4

    with get_connection(":memory:") as conn:
        # New in-memory conn is fresh; re-write to verify same call works idempotently
        init_db(conn)
        fetch_series(S, conn, api_key=API_KEY, client=FakeHTTPClient(FakeJSONResponse(200, payload=OBSERVATIONS_OK)))
        rows = list(conn.execute(
            "SELECT series_id, obs_date, value, label, units, source FROM observations "
            "WHERE series_id = ? ORDER BY obs_date DESC",
            ("DGS10",),
        ))
        assert len(rows) == 4
        assert rows[0]["obs_date"] == "2026-04-30"
        assert rows[0]["value"] == 4.21
        assert rows[0]["label"] == "10-year Treasury yield"
        assert rows[0]["units"] == "%"
        assert rows[0]["source"] == "fred"
        # The 2026-04-28 row had value '.' → should be None
        for r in rows:
            if r["obs_date"] == "2026-04-28":
                assert r["value"] is None


def test_fetch_series_upsert_idempotent() -> None:
    """Running the same fetch twice must not create duplicate rows."""
    client1 = FakeHTTPClient(FakeJSONResponse(200, payload=OBSERVATIONS_OK))
    client2 = FakeHTTPClient(FakeJSONResponse(200, payload=OBSERVATIONS_OK))
    with get_connection(":memory:") as conn:
        init_db(conn)
        fetch_series(S, conn, api_key=API_KEY, client=client1)
        fetch_series(S, conn, api_key=API_KEY, client=client2)
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM observations WHERE series_id = ?",
            ("DGS10",),
        ).fetchone()["c"]
    assert count == 4


def test_fetch_series_upsert_replaces_revised_value() -> None:
    """If FRED revises a value (rare but real), the new value wins."""
    first = {"observations": [{"date": "2026-04-30", "value": "4.21"}]}
    revised = {"observations": [{"date": "2026-04-30", "value": "4.20"}]}

    with get_connection(":memory:") as conn:
        init_db(conn)
        fetch_series(S, conn, api_key=API_KEY,
                     client=FakeHTTPClient(FakeJSONResponse(200, payload=first)))
        fetch_series(S, conn, api_key=API_KEY,
                     client=FakeHTTPClient(FakeJSONResponse(200, payload=revised)))
        row = conn.execute(
            "SELECT value FROM observations WHERE series_id=? AND obs_date=?",
            ("DGS10", "2026-04-30"),
        ).fetchone()

    assert row["value"] == 4.20


# =============================================================================
# get_latest_observation
# =============================================================================

def test_get_latest_observation_returns_most_recent() -> None:
    client = FakeHTTPClient(FakeJSONResponse(200, payload=OBSERVATIONS_OK))
    with get_connection(":memory:") as conn:
        init_db(conn)
        fetch_series(S, conn, api_key=API_KEY, client=client)
        latest = get_latest_observation(conn, "DGS10")

    assert latest is not None
    assert latest["obs_date"] == "2026-04-30"
    assert latest["value"] == 4.21
    assert latest["label"] == "10-year Treasury yield"
    assert latest["units"] == "%"


def test_get_latest_observation_returns_none_when_missing() -> None:
    with get_connection(":memory:") as conn:
        init_db(conn)
        assert get_latest_observation(conn, "NONEXISTENT") is None


# =============================================================================
# Auth / error paths
# =============================================================================

def test_empty_api_key_short_circuits() -> None:
    client = FakeHTTPClient(FakeJSONResponse(200, payload=OBSERVATIONS_OK))
    with get_connection(":memory:") as conn:
        init_db(conn)
        outcome = fetch_series(S, conn, api_key="", client=client)

    assert outcome.status == 401
    assert "FRED_API_KEY" in (outcome.error or "")
    assert outcome.rows_written == 0
    assert client.calls == []


def test_api_key_appears_in_url_as_query_param() -> None:
    """
    UNLIKE Finnhub, FRED only accepts api_key as a URL query parameter
    (no header option). This test documents that fact — security-conscious
    callers should know.
    """
    client = FakeHTTPClient(FakeJSONResponse(200, payload=OBSERVATIONS_OK))
    with get_connection(":memory:") as conn:
        init_db(conn)
        fetch_series(S, conn, api_key=API_KEY, client=client)

    url, _ = client.calls[0]
    assert f"api_key={API_KEY}" in url


def test_non_200_records_status() -> None:
    client = FakeHTTPClient(FakeJSONResponse(500))
    with get_connection(":memory:") as conn:
        init_db(conn)
        outcome = fetch_series(S, conn, api_key=API_KEY, client=client)
    assert outcome.status == 500
    assert outcome.rows_written == 0


def test_400_bad_request_records_status() -> None:
    """FRED returns 400 for invalid series_id / bad params."""
    client = FakeHTTPClient(FakeJSONResponse(400))
    with get_connection(":memory:") as conn:
        init_db(conn)
        outcome = fetch_series(S, conn, api_key=API_KEY, client=client)
    assert outcome.status == 400


def test_malformed_json_records_parse_error() -> None:
    client = FakeHTTPClient(FakeJSONResponse(200, broken_body="not-json"))
    with get_connection(":memory:") as conn:
        init_db(conn)
        outcome = fetch_series(S, conn, api_key=API_KEY, client=client)
    assert outcome.status == -3
    assert "json parse" in (outcome.error or "")


def test_missing_observations_array_records_parse_error() -> None:
    client = FakeHTTPClient(FakeJSONResponse(200, payload={"count": 0}))
    with get_connection(":memory:") as conn:
        init_db(conn)
        outcome = fetch_series(S, conn, api_key=API_KEY, client=client)
    assert outcome.status == -3


def test_timeout_records_negative_one() -> None:
    def boom(u: str, h: dict[str, str]) -> Any:
        raise httpx.ReadTimeout("read timeout")
    client = FakeHTTPClient(boom)
    with get_connection(":memory:") as conn:
        init_db(conn)
        outcome = fetch_series(S, conn, api_key=API_KEY, client=client)
    assert outcome.status == -1


def test_connection_error_records_negative_two() -> None:
    def boom(u: str, h: dict[str, str]) -> Any:
        raise httpx.ConnectError("dns failed")
    client = FakeHTTPClient(boom)
    with get_connection(":memory:") as conn:
        init_db(conn)
        outcome = fetch_series(S, conn, api_key=API_KEY, client=client)
    assert outcome.status == -2


# =============================================================================
# Defensive parsing
# =============================================================================

def test_skips_observations_with_missing_date() -> None:
    payload = {"observations": [
        {"date": "", "value": "4.21"},
        {"value": "4.18"},  # no 'date' key
        {"date": "2026-04-30", "value": "4.15"},
    ]}
    client = FakeHTTPClient(FakeJSONResponse(200, payload=payload))
    with get_connection(":memory:") as conn:
        init_db(conn)
        outcome = fetch_series(S, conn, api_key=API_KEY, client=client)
        count = conn.execute("SELECT COUNT(*) AS c FROM observations").fetchone()["c"]
    assert outcome.rows_written == 1
    assert count == 1


def test_skips_non_dict_array_elements() -> None:
    payload = {"observations": [
        "not a dict",
        None,
        {"date": "2026-04-30", "value": "4.21"},
        42,
    ]}
    client = FakeHTTPClient(FakeJSONResponse(200, payload=payload))
    with get_connection(":memory:") as conn:
        init_db(conn)
        outcome = fetch_series(S, conn, api_key=API_KEY, client=client)
    assert outcome.rows_written == 1


# =============================================================================
# fetch_all + env helper
# =============================================================================

def test_fetch_all_polls_every_series(monkeypatch) -> None:
    fake = FakeHTTPClient(lambda u, h: FakeJSONResponse(200, payload=OBSERVATIONS_OK))
    monkeypatch.setattr("ingestion.fred.httpx.Client", lambda *a, **k: fake)

    with get_connection(":memory:") as conn:
        init_db(conn)
        outcomes = fetch_all(conn, api_key=API_KEY)

    assert len(outcomes) == len(FRED_SERIES)
    assert all(o.status == 200 for o in outcomes)


def test_fetch_all_continues_past_failed_series(monkeypatch) -> None:
    def responder(url: str, headers: dict[str, str]) -> Any:
        if "DGS10" in url:
            return FakeJSONResponse(500)
        return FakeJSONResponse(200, payload=OBSERVATIONS_OK)
    fake = FakeHTTPClient(responder)
    monkeypatch.setattr("ingestion.fred.httpx.Client", lambda *a, **k: fake)

    with get_connection(":memory:") as conn:
        init_db(conn)
        outcomes = fetch_all(conn, api_key=API_KEY)

    statuses = [o.status for o in outcomes]
    assert 200 in statuses
    assert 500 in statuses


def test_fetch_all_from_env_reads_key(monkeypatch) -> None:
    fake = FakeHTTPClient(lambda u, h: FakeJSONResponse(200, payload=OBSERVATIONS_OK))
    monkeypatch.setattr("ingestion.fred.httpx.Client", lambda *a, **k: fake)
    monkeypatch.setenv("FRED_API_KEY", "env_key")

    with get_connection(":memory:") as conn:
        init_db(conn)
        outcomes = fetch_all_from_env(conn)

    assert all(o.status == 200 for o in outcomes)
    for url, _ in fake.calls:
        assert "api_key=env_key" in url


def test_fetch_all_from_env_missing_key_short_circuits(monkeypatch) -> None:
    fake = FakeHTTPClient(lambda u, h: FakeJSONResponse(200, payload=OBSERVATIONS_OK))
    monkeypatch.setattr("ingestion.fred.httpx.Client", lambda *a, **k: fake)
    monkeypatch.delenv("FRED_API_KEY", raising=False)

    with get_connection(":memory:") as conn:
        init_db(conn)
        outcomes = fetch_all_from_env(conn)

    assert all(o.status == 401 for o in outcomes)
    assert fake.calls == []


# =============================================================================
# Catalog sanity
# =============================================================================

def test_fred_series_catalog_well_formed() -> None:
    seen: set[str] = set()
    for s in FRED_SERIES:
        assert s.series_id, "empty series_id"
        assert s.label, "empty label"
        assert s.series_id not in seen, f"dup: {s.series_id}"
        seen.add(s.series_id)


def test_fred_series_covers_brief_required_signals() -> None:
    """Smoke check: brief lists SPX, UST yields, DXY-ish, EUR/USD, USD/JPY,
    USD/CNY, gold. Confirm we cover the mappings.

    Note (2026-04-23): gold (GOLDAMGBD228NLBM) was discontinued by FRED;
    gold direction now comes from news mentions / Finnhub. See fred.py
    for the documented removal.
    """
    ids = {s.series_id for s in FRED_SERIES}
    assert "SP500" in ids
    assert "DGS10" in ids
    assert "DEXUSEU" in ids
    assert "DEXJPUS" in ids
    assert "DEXCHUS" in ids
    assert "DTWEXBGS" in ids  # broad-basket USD index, our DXY proxy
    # Gold deliberately absent; covered via news rather than FRED.
    assert "GOLDAMGBD228NLBM" not in ids
