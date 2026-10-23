from __future__ import annotations

import json
import math
import statistics
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def config() -> dict:
    return json.loads((ROOT / "config" / "universe.json").read_text(encoding="utf-8"))


def symbols(cfg: dict) -> list[str]:
    out = {cfg["benchmark"], cfg["growth_benchmark"], cfg["volatility_symbol"]}
    out.update(cfg["sectors"].keys())
    for tickers in cfg["sectors"].values(): out.update(tickers)
    return sorted(out)


def download_daily(symbol: str, lookback_days: int, minimum_bars: int = 252) -> list[tuple]:
    """Download actual OHLCV daily bars from Yahoo. Raises on invalid data."""
    end = int(time.time())
    start = int((datetime.now(timezone.utc) - timedelta(days=lookback_days * 2)).timestamp())
    encoded = urllib.parse.quote(symbol, safe="^")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?period1={start}&period2={end}&interval=1d&events=history"
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 EMA-Cross-Dashboard/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    result = payload.get("chart", {}).get("result")
    if not result: raise ValueError(f"Yahoo did not return data for {symbol}")
    result = result[0]; quote = result["indicators"]["quote"][0]
    rows = []
    for timestamp, op, hi, lo, cl, vol in zip(result["timestamp"], quote["open"], quote["high"], quote["low"], quote["close"], quote["volume"]):
        values = (op, hi, lo, cl, vol)
        if any(value is None for value in values) or cl <= 0 or hi < max(op, cl, lo) or lo > min(op, cl, hi):
            continue
        date = datetime.fromtimestamp(timestamp, tz=timezone.utc).date().isoformat()
        rows.append((date, float(op), float(hi), float(lo), float(cl), float(vol)))
    if len(rows) < minimum_bars: raise ValueError(f"Insufficient valid daily bars for {symbol}: {len(rows)} (minimum {minimum_bars})")
    return rows


def sma(values: list[float], length: int) -> float | None:
    return statistics.fmean(values[-length:]) if len(values) >= length else None


def ema(values: list[float], length: int) -> float | None:
    if len(values) < length: return None
    result = statistics.fmean(values[:length]); alpha = 2 / (length + 1)
    for value in values[length:]: result += alpha * (value - result)
    return result


def wma(values: list[float], length: int) -> float | None:
    if len(values) < length: return None
    weights = list(range(1, length + 1)); return sum(a*b for a,b in zip(values[-length:], weights)) / sum(weights)


def percentile_rank(values: list[float], current: float) -> float | None:
    if not values or current is None: return None
    return 100 * sum(value <= current for value in values) / len(values)


def safe_div(n: float | None, d: float | None) -> float | None:
    return n / d if n is not None and d not in (None, 0) else None


def pct_change(values: list[float], sessions: int) -> float | None:
    return safe_div(values[-1], values[-sessions - 1]) - 1 if len(values) > sessions else None


def fmt(value: float | None, decimals: int = 2) -> float | None:
    return round(value, decimals) if value is not None and math.isfinite(value) else None
