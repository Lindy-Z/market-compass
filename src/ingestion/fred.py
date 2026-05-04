"""
market-compass ingestion — FRED (Federal Reserve Economic Data)
==============================================================================
FRED API client. Numerical time-series for the macro / NA-FX track —
UST yield curve, FX pairs, SPX, VIX, gold, CPI, etc.
FRED 数值时序抓取器 — 美债收益率曲线、汇率、标普、VIX、黄金、CPI 等。

Design notes / 设计说明
----------------------
- **Data goes into ``observations`` table, not ``items``.** Numerical
  time-series doesn't fit the news-article shape (no title, no body, no
  causal chain). The reasoning engine queries ``observations`` directly
  for "latest 10Y yield"-style lookups when assembling the brief.
  数据写入 ``observations`` 表,不进 ``items`` — 详见 schema.sql 的 v3 注释。

- **Auth via URL query parameter.** FRED's API doesn't accept the API key
  in any header — only ``?api_key=...``. Less secure than header auth
  (the key shows up in proxy logs / URL caches), but unavoidable. The
  pre-commit hook is the last line of defense against an actual key
  being committed; the in-code key only ever lives in env vars + the
  request URL.
  FRED 仅支持 URL 查询参数鉴权,不接受 header。安全性较弱,但无可奈何;
  pre-commit 钩子是最后防线。

- **No throttle.** FRED's free tier is 120 calls/min. Our usage is
  ~14 calls/run × 1 run/day = trivial. We could add a throttle if a
  busier polling pattern becomes useful.
  FRED 免费档限速 120/min,我们只跑 ~14 calls/day,无需节流。
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Optional
from urllib.parse import urlencode

import httpx

log = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Constants / 常量
# -----------------------------------------------------------------------------

DEFAULT_BASE_URL: str = "https://api.stlouisfed.org/fred"
DEFAULT_TIMEOUT_SEC: float = 15.0
DEFAULT_USER_AGENT: str = (
    "market-compass/0.1 (+https://github.com/Lindy-Z/market-compass)"
)
#: How many recent observations to ask for per series. Enough to backfill
#: a few missing days without ballooning response size.
DEFAULT_LIMIT: int = 30


# -----------------------------------------------------------------------------
# Series catalog / 数据系列目录
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class SeriesConfig:
    """One FRED series we ingest.

    Args:
        series_id: FRED series identifier (e.g. ``'DGS10'``). Persisted
            into ``observations.series_id``; stable for the life of the
            data (don't rename without a migration).
        label: Human-readable name (e.g. ``'10-year Treasury yield'``).
        units: Short units string (e.g. ``'%'``, ``'USD/EUR'``).
        track_hint: Suggested track for the reasoning layer. Default
            ``'na_fx'`` since most curated series are NA market / FX
            related.
        notes: Free-text notes (publication frequency, caveats).
    """
    series_id: str
    label: str
    units: str = ""
    track_hint: Optional[str] = "na_fx"
    notes: str = ""


#: Curated series — covers the brief's required signals (SPX, UST yields,
#: DXY proxy, EUR/USD, USD/JPY, USD/CNY, gold) plus core macro indicators.
#: 核心系列, 覆盖 brief 要求的方向性信号 (SPX, UST 曲线, DXY-代理, EUR/USD, USD/JPY,
#: USD/CNY, 黄金) 加几条宏观核心 (CPI, 失业率, 联邦基金).
FRED_SERIES: list[SeriesConfig] = [
    # ---- UST yield curve ----
    SeriesConfig("DGS3MO", "3-month Treasury constant maturity rate",  units="%"),
    SeriesConfig("DGS2",   "2-year Treasury constant maturity rate",   units="%"),
    SeriesConfig("DGS5",   "5-year Treasury constant maturity rate",   units="%"),
    SeriesConfig("DGS10",  "10-year Treasury constant maturity rate",  units="%"),
    SeriesConfig("DGS30",  "30-year Treasury constant maturity rate",  units="%"),
    # ---- FX (mostly NY-noon fixings) ----
    SeriesConfig("DEXUSEU", "USD per EUR (noon NY rate)",              units="USD/EUR"),
    SeriesConfig("DEXJPUS", "JPY per USD (noon NY rate)",              units="JPY/USD"),
    SeriesConfig("DEXCHUS", "CNY per USD (noon NY rate)",              units="CNY/USD"),
    SeriesConfig("DTWEXBGS", "Trade-weighted USD index (broad, goods+services)",
                 units="index 2006=100"),
    # ---- Equity / vol ----
    SeriesConfig("SP500",   "S&P 500 index level",                     units="index"),
    SeriesConfig("VIXCLS",  "CBOE Volatility Index (close)",           units="index"),
    # ---- Commodities ----
    # GOLDAMGBD228NLBM (LBMA Gold PM fixing) was discontinued by FRED after
    # ICE Benchmark Administration changed the licensing terms; FRED returns
    # HTTP 400 on the series since 2025. There's no clean FRED replacement
    # for daily gold price. Gold direction is now sourced from:
    #   - Finnhub `general` / `forex` news mentions
    #   - news headlines that name a level (the LLM extracts the print)
    # Re-add here if FRED ever republishes a gold series.
    # GOLDAMGBD228NLBM 已被 FRED 下架,无干净替代;黄金方向改由 Finnhub 新闻
    # 与标题中提及的价格水平获取。
    # ---- Macro headline ----
    SeriesConfig("CPIAUCSL", "CPI for all urban consumers (all items)", units="index 1982-84=100",
                 notes="monthly; lagged ~2 weeks"),
    SeriesConfig("UNRATE",   "Unemployment rate (U-3)",                 units="%",
                 notes="monthly; first Friday release"),
    SeriesConfig("DFF",      "Federal funds effective rate (daily)",    units="%"),
]


# -----------------------------------------------------------------------------
# Outcome / 结果对象
# -----------------------------------------------------------------------------

@dataclass
class FREDOutcome:
    """One series's fetch result.

    Distinct from ``rss.FetchOutcome`` because FRED writes to a different
    table and doesn't produce news-shaped items.

    与 ``rss.FetchOutcome`` 区分: FRED 写到不同的表,且无 news-shape items。
    """
    series: SeriesConfig
    rows_written: int = 0
    status: int = 0
    error: Optional[str] = None


# -----------------------------------------------------------------------------
# Helpers / 工具
# -----------------------------------------------------------------------------

def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_value(raw: Any) -> Optional[float]:
    """Convert a FRED observation's ``value`` to a float, or ``None``.

    FRED uses the literal string ``'.'`` for missing observations
    (holidays, weekends, before-launch dates). Empty strings and
    non-numeric values also become ``None``.

    FRED 用 '.' 表示缺失值;空串与非数值同样视为 None。
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or s == ".":
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _build_url(series_id: str, api_key: str, limit: int, base_url: str) -> str:
    """Construct the FRED /series/observations URL."""
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "sort_order": "desc",
        "limit": str(limit),
    }
    return f"{base_url}/series/observations?{urlencode(params)}"


# -----------------------------------------------------------------------------
# Persistence / 持久化
# -----------------------------------------------------------------------------

def _upsert_observation(
    conn: sqlite3.Connection,
    *,
    series_id: str,
    obs_date: str,
    value: Optional[float],
    label: str,
    units: str,
    fetched_ts: str,
) -> None:
    """Insert-or-replace one observation row."""
    conn.execute(
        """
        INSERT INTO observations (
            series_id, obs_date, value, label, units, source, fetched_ts
        ) VALUES (?, ?, ?, ?, ?, 'fred', ?)
        ON CONFLICT(series_id, obs_date) DO UPDATE SET
            value      = excluded.value,
            label      = excluded.label,
            units      = excluded.units,
            fetched_ts = excluded.fetched_ts
        """,
        (series_id, obs_date, value, label, units, fetched_ts),
    )


def get_latest_observation(
    conn: sqlite3.Connection,
    series_id: str,
) -> Optional[dict[str, Any]]:
    """
    Return the most-recent (by ``obs_date``) row for ``series_id``, or
    ``None`` if we have no observations for that series.

    Used by the reasoning engine to fetch "current level" values.
    供推理层取"当前水平"值的便捷查询。
    """
    row = conn.execute(
        """
        SELECT series_id, obs_date, value, label, units, source, fetched_ts
        FROM observations WHERE series_id = ?
        ORDER BY obs_date DESC LIMIT 1
        """,
        (series_id,),
    ).fetchone()
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


# -----------------------------------------------------------------------------
# Public API / 公共 API
# -----------------------------------------------------------------------------

def fetch_series(
    series: SeriesConfig,
    conn: sqlite3.Connection,
    *,
    api_key: str,
    client: Optional[httpx.Client] = None,
    limit: int = DEFAULT_LIMIT,
    base_url: str = DEFAULT_BASE_URL,
    timeout: float = DEFAULT_TIMEOUT_SEC,
    user_agent: str = DEFAULT_USER_AGENT,
) -> FREDOutcome:
    """
    Fetch up to ``limit`` most recent observations for one series and
    upsert them into the ``observations`` table.

    Args:
        series: configured series.
        conn: open SQLite connection.
        api_key: FRED API key (sent as URL query param — see module
            docstring). Empty key short-circuits to a 401-shaped outcome
            with no HTTP call made.
        client: pre-built ``httpx.Client`` to share across calls.
        limit: how many recent observations to ask for.
        base_url: override for tests; defaults to FRED production.
        timeout: request timeout (used only when no ``client``).
        user_agent: ``User-Agent`` header value.

    Returns:
        ``FREDOutcome`` with ``rows_written`` populated on success.
    """
    if not api_key:
        return FREDOutcome(
            series=series, rows_written=0,
            status=401,
            error="missing FRED_API_KEY (empty); refusing to call API",
        )

    url = _build_url(series.series_id, api_key, limit, base_url)
    headers = {"Accept": "application/json", "User-Agent": user_agent}

    own_client = client is None
    if own_client:
        client = httpx.Client(timeout=timeout, follow_redirects=True)

    fetched_ts = _now_utc_iso()
    try:
        try:
            response = client.get(url, headers=headers)
        except httpx.TimeoutException as e:
            log.warning("FRED timeout %s: %s", series.series_id, e)
            return FREDOutcome(series=series, status=-1, error=f"timeout: {e}")
        except httpx.HTTPError as e:
            log.warning("FRED http error %s: %s", series.series_id, e)
            return FREDOutcome(series=series, status=-2, error=f"http error: {e}")

        if response.status_code != 200:
            return FREDOutcome(
                series=series, status=response.status_code,
                error=f"non-200 status: {response.status_code}",
            )

        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError) as e:
            return FREDOutcome(series=series, status=-3, error=f"json parse error: {e}")

        if not isinstance(payload, dict):
            return FREDOutcome(
                series=series, status=-3,
                error=f"unexpected payload shape: {type(payload).__name__}",
            )

        observations = payload.get("observations")
        if not isinstance(observations, list):
            return FREDOutcome(
                series=series, status=-3,
                error="payload missing 'observations' array",
            )

        rows = 0
        for obs in observations:
            if not isinstance(obs, dict):
                continue
            obs_date = (obs.get("date") or "").strip()
            if not obs_date:
                continue
            _upsert_observation(
                conn,
                series_id=series.series_id,
                obs_date=obs_date,
                value=_parse_value(obs.get("value")),
                label=series.label,
                units=series.units,
                fetched_ts=fetched_ts,
            )
            rows += 1

        return FREDOutcome(
            series=series, rows_written=rows, status=200, error=None,
        )
    finally:
        if own_client:
            client.close()


def fetch_all(
    conn: sqlite3.Connection,
    *,
    api_key: str,
    series: Iterable[SeriesConfig] = FRED_SERIES,
    limit: int = DEFAULT_LIMIT,
    base_url: str = DEFAULT_BASE_URL,
    timeout: float = DEFAULT_TIMEOUT_SEC,
    user_agent: str = DEFAULT_USER_AGENT,
) -> list[FREDOutcome]:
    """Fetch every series sequentially using one shared ``httpx.Client``."""
    outcomes: list[FREDOutcome] = []
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        for s in series:
            outcomes.append(fetch_series(
                s, conn,
                api_key=api_key, client=client, limit=limit,
                base_url=base_url, user_agent=user_agent,
            ))
    return outcomes


def fetch_all_from_env(
    conn: sqlite3.Connection,
    **kwargs: Any,
) -> list[FREDOutcome]:
    """Read ``FRED_API_KEY`` from env and call :func:`fetch_all`."""
    api_key = os.environ.get("FRED_API_KEY", "").strip()
    return fetch_all(conn, api_key=api_key, **kwargs)


__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_LIMIT",
    "DEFAULT_TIMEOUT_SEC",
    "DEFAULT_USER_AGENT",
    "FRED_SERIES",
    "FREDOutcome",
    "SeriesConfig",
    "fetch_all",
    "fetch_all_from_env",
    "fetch_series",
    "get_latest_observation",
]
