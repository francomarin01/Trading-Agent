"""Point-in-time research for alternative versions of the four-pillar score.

This module is intentionally isolated from the production screener and its
weights. It reads the existing daily OHLCV database and writes research-only
artifacts under reports/score_research/.
"""
from __future__ import annotations

import csv
import json
import math
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .engine import _clip, _ema_series as engine_ema_series, _sma_series as engine_sma_series
from .market import config
from .storage import connect, load_bars

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "reports" / "score_research"
HORIZONS = (5, 10, 20)
THRESHOLDS = (65, 70, 75, 80)
MAX_SCORE = 99.5
PIVOT_LEFT = 5
PIVOT_RIGHT = 5
ROUND_TRIP_COST_BPS = 10


def _ema(values: list[float | None], length: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    valid = [(i, v) for i, v in enumerate(values) if v is not None]
    if len(valid) < length:
        return out
    seed_items = valid[:length]
    seed_i, _ = seed_items[-1]
    value = statistics.fmean(v for _, v in seed_items)
    out[seed_i] = value
    alpha = 2 / (length + 1)
    for i, v in valid[length:]:
        value += alpha * (v - value)
        out[i] = value
    return out


def _rsi(closes: list[float], length: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= length:
        return out
    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(0.0, x) for x in changes]
    losses = [max(0.0, -x) for x in changes]
    avg_gain = statistics.fmean(gains[:length])
    avg_loss = statistics.fmean(losses[:length])

    def value(gain: float, loss: float) -> float:
        if loss == 0:
            return 100.0 if gain else 50.0
        return 100 - 100 / (1 + gain / loss)

    out[length] = value(avg_gain, avg_loss)
    for i in range(length + 1, len(closes)):
        change = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (length - 1) + max(change, 0.0)) / length
        avg_loss = (avg_loss * (length - 1) + max(-change, 0.0)) / length
        out[i] = value(avg_gain, avg_loss)
    return out


def indicators(bars: list[dict]) -> dict[str, list[float | None]]:
    """Causal daily features; pivots appear only on their confirmation bar."""
    n = len(bars)
    close = [float(b["close"]) for b in bars]
    high = [float(b["high"]) for b in bars]
    low = [float(b["low"]) for b in bars]
    volume = [float(b["volume"]) for b in bars]
    rsi = _rsi(close)
    macd_line = [None] * n
    fast, slow = _ema(close, 12), _ema(close, 26)
    for i in range(n):
        if fast[i] is not None and slow[i] is not None:
            macd_line[i] = fast[i] - slow[i]
    signal = _ema(macd_line, 9)
    macd_hist = [None if macd_line[i] is None or signal[i] is None else macd_line[i] - signal[i] for i in range(n)]

    vwap20: list[float | None] = [None] * n
    for i in range(19, n):
        den = sum(volume[i - 19:i + 1])
        if den:
            vwap20[i] = sum(((high[j] + low[j] + close[j]) / 3) * volume[j] for j in range(i - 19, i + 1)) / den

    pivot_low: list[float | None] = [None] * n
    pivot_high: list[float | None] = [None] * n
    avwap: list[float | None] = [None] * n
    latest_low = latest_high = None
    anchor_index = None
    typical_volume = [((high[i] + low[i] + close[i]) / 3) * volume[i] for i in range(n)]
    cum_pv = [0.0]
    cum_v = [0.0]
    for pv, vol in zip(typical_volume, volume):
        cum_pv.append(cum_pv[-1] + pv)
        cum_v.append(cum_v[-1] + vol)
    for i in range(n):
        candidate = i - PIVOT_RIGHT
        if candidate >= PIVOT_LEFT:
            lo_window = low[candidate - PIVOT_LEFT:candidate + PIVOT_RIGHT + 1]
            hi_window = high[candidate - PIVOT_LEFT:candidate + PIVOT_RIGHT + 1]
            if low[candidate] == min(lo_window) and lo_window.count(low[candidate]) == 1:
                latest_low = low[candidate]
                anchor_index = candidate
            if high[candidate] == max(hi_window) and hi_window.count(high[candidate]) == 1:
                latest_high = high[candidate]
        pivot_low[i] = latest_low
        pivot_high[i] = latest_high
        if anchor_index is not None:
            denom = cum_v[i + 1] - cum_v[anchor_index]
            if denom:
                avwap[i] = (cum_pv[i + 1] - cum_pv[anchor_index]) / denom
    return {"rsi14": rsi, "macd_hist": macd_hist, "vwap20": vwap20,
            "avwap_pivot_low": avwap, "pivot_support": pivot_low,
            "pivot_resistance": pivot_high}


def overlay_points(name: str, i: int, close: float, feat: dict[str, list[float | None]]) -> int:
    rsi = feat["rsi14"][i]
    hist = feat["macd_hist"][i]
    prior_hist = feat["macd_hist"][i - 1] if i else None
    vwap = feat["vwap20"][i]
    avwap = feat["avwap_pivot_low"][i]
    support = feat["pivot_support"][i]
    resistance = feat["pivot_resistance"][i]
    rsi_pts = 0 if rsi is None else (3 if 50 <= rsi <= 65 else 1 if 45 <= rsi < 50 or 65 < rsi <= 72 else -3 if rsi < 40 or rsi > 78 else 0)
    macd_pts = 0 if hist is None or prior_hist is None else (3 if hist > 0 and hist > prior_hist else 1 if hist > 0 else -3 if hist < 0 and hist < prior_hist else -1)
    vwap_pts = 0
    if vwap is not None and avwap is not None:
        above_vwap, above_avwap = close >= vwap, close >= avwap
        vwap_pts = 3 if above_vwap and above_avwap else -3 if not above_vwap and not above_avwap else 1 if above_vwap else -1
    pivot_pts = 0
    if resistance is not None and close > resistance:
        pivot_pts = 3
    elif support is not None:
        pivot_pts = 2 if support <= close <= support * 1.03 else -3 if close < support else 0
    if name == "RSI14": return rsi_pts
    if name == "MACD": return macd_pts
    if name == "VWAP_AVWAP": return vwap_pts
    if name == "PIVOT_SR": return pivot_pts
    if name == "COMBINED_CAPPED": return max(-8, min(8, rsi_pts + macd_pts + vwap_pts + pivot_pts))
    return 0


def _metrics(rows: list[dict]) -> dict | None:
    if not rows:
        return None
    rets = [r["return_pct"] for r in rows]
    excess = [r["excess_pct"] for r in rows]
    wins = sum(x > 0 for x in rets)
    excess_wins = sum(x > 0 for x in excess)
    return {
        "signals": len(rows), "win_rate_pct": round(100 * wins / len(rows), 2),
        "excess_win_rate_pct": round(100 * excess_wins / len(rows), 2),
        "mean_return_pct": round(statistics.fmean(rets), 3),
        "median_return_pct": round(statistics.median(rets), 3),
        "mean_benchmark_pct": round(statistics.fmean(r["benchmark_pct"] for r in rows), 3),
        "mean_excess_pct": round(statistics.fmean(excess), 3),
    }


def _score_variant(base_score: float, pillars: dict, variant: str, points: int) -> float:
    if variant == "WEIGHT_25_30_30_15":
        p = pillars
        return min(MAX_SCORE, (p["trend"] / 30 * 25) + (p["relative_strength"] / 25 * 30) + (p["contraction"] / 20 * 30) + (p["setup"] / 25 * 15))
    if variant == "BASELINE":
        return base_score
    return max(0.0, min(MAX_SCORE, base_score + points))


def baseline_score_series(bars: list[dict], spy_bars: list[dict], sector_bars: list[dict]) -> list[tuple[float | None, dict]]:
    """Vector-style causal equivalent of engine.four_pillar_score per close."""
    closes = [float(b["close"]) for b in bars]
    highs = [float(b["high"]) for b in bars]
    lows = [float(b["low"]) for b in bars]
    volumes = [float(b["volume"]) for b in bars]
    spy_close = [float(b["close"]) for b in spy_bars]
    sector_close = [float(b["close"]) for b in sector_bars]
    spy_idx = {b["date"]: i for i, b in enumerate(spy_bars)}
    sector_idx = {b["date"]: i for i, b in enumerate(sector_bars)}
    e9, e21 = engine_ema_series(closes, 9), engine_ema_series(closes, 21)
    e200, sma50 = engine_ema_series(closes, 200), engine_sma_series(closes, 50)
    ema21_for_slope = engine_ema_series(closes, 21)
    vol10, vol50 = engine_sma_series(volumes, 10), engine_sma_series(volumes, 50)
    tr = [None] * len(bars)
    for i in range(1, len(bars)):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))

    def rolling_mean(values, length):
        out = [None] * len(values)
        window = []
        total = 0.0
        for i, value in enumerate(values):
            window.append(value)
            if value is not None:
                total += value
            if len(window) > length:
                old = window.pop(0)
                if old is not None:
                    total -= old
            if len(window) == length and all(x is not None for x in window):
                out[i] = total / length
        return out

    atr14, atr50 = rolling_mean(tr, 14), rolling_mean(tr, 50)

    def change(values, i, length):
        return values[i] / values[i - length] - 1 if i >= length and values[i - length] else None

    results = [(None, {}) for _ in bars]
    for i, bar in enumerate(bars):
        si, ei = spy_idx.get(bar["date"]), sector_idx.get(bar["date"])
        if si is None or ei is None or i < 200 or None in (e9[i], e21[i], e200[i], sma50[i], vol10[i], vol50[i], atr14[i], atr50[i]) or si < 60 or ei < 20:
            continue
        slope_base = ema21_for_slope[i - 20] if i >= 20 else None
        slope21 = e21[i] / slope_base - 1 if slope_base else None
        stock20, spy20, sector20 = change(closes, i, 20), change(spy_close, si, 20), change(sector_close, ei, 20)
        stock60, spy60 = change(closes, i, 60), change(spy_close, si, 60)
        rel20 = stock20 - spy20 if stock20 is not None and spy20 is not None else None
        rel60 = stock60 - spy60 if stock60 is not None and spy60 is not None else None
        sector_rel = sector20 - spy20 if sector20 is not None and spy20 is not None else None
        atr_ratio = atr14[i] / atr50[i] if atr50[i] else None
        range20 = (max(closes[i - 19:i + 1]) - min(closes[i - 19:i + 1])) / closes[i]
        volume_contraction = vol10[i] / vol50[i] if vol50[i] else None
        volume_ratio = volumes[i] / vol50[i] if vol50[i] else None
        gap_fast = (e9[i] - e21[i]) / atr14[i] if atr14[i] else None
        gap_mid = (e21[i] - sma50[i]) / atr14[i] if atr14[i] else None
        extension = (closes[i] - e21[i]) / atr14[i] if atr14[i] else None
        near_high = closes[i] / max(closes[i - 19:i + 1])
        trend = (10 * _clip((closes[i] / e200[i] - .97) / .10) +
                 4 * _clip(gap_fast / 1.5 if gap_fast is not None else None) +
                 4 * _clip(gap_mid / 3 if gap_mid is not None else None) +
                 7 * _clip(slope21 / .05 if slope21 is not None else None) +
                 5 * _clip((closes[i] / sma50[i] - .98) / .06))
        rs = (12 * _clip(rel20 / .12 if rel20 is not None else None) +
              8 * _clip(rel60 / .20 if rel60 is not None else None) +
              5 * _clip(sector_rel / .10 if sector_rel is not None else None))
        contraction = (8 * _clip((1.10 - (atr_ratio or 1.10)) / .35) +
                       7 * _clip((.30 - (range20 or .30)) / .20) +
                       5 * _clip((1.15 - (volume_contraction or 1.15)) / .50))
        setup = (5 * _clip(gap_fast / 1.5 if gap_fast is not None else None) +
                 6 * _clip((near_high - .90) / .10) +
                 4 * _clip(((volume_ratio or 0) - .80) / .70) +
                 4 * _clip(1 - abs((extension or 0) - .75) / 2) +
                 6 * _clip((closes[i] / sma50[i] - .98) / .06))
        score = round(min(MAX_SCORE, trend + rs + contraction + setup), 2)
        results[i] = (score, {"pillars": {"trend": round(trend, 2), "relative_strength": round(rs, 2), "contraction": round(contraction, 2), "setup": round(setup, 2)}})
    return results


def generate_observations(bars_by_symbol: dict[str, list[dict]], cfg: dict) -> list[dict]:
    """Calculate threshold-cross observations for each model and horizon."""
    spy = bars_by_symbol.get(cfg["benchmark"], [])
    date_to_index = {s: {bar["date"]: i for i, bar in enumerate(rows)} for s, rows in bars_by_symbol.items()}
    observations = []
    cost_pct = ROUND_TRIP_COST_BPS / 100
    models = ("BASELINE", "WEIGHT_25_30_30_15", "RSI14", "MACD", "VWAP_AVWAP", "PIVOT_SR", "COMBINED_CAPPED")
    for etf, tickers in cfg["sectors"].items():
        etf_bars = bars_by_symbol.get(etf, [])
        etf_lookup = date_to_index.get(etf, {})
        for ticker in tickers:
            bars = bars_by_symbol.get(ticker, [])
            if len(bars) < 252:
                continue
            feat = indicators(bars)
            # Keep benchmark calendars intact; align each score by signal date.
            base_series = baseline_score_series(bars, spy, etf_bars)
            previous = {(model, threshold): False for model in models for threshold in THRESHOLDS}
            last_signal = {(model, threshold): -10_000 for model in models for threshold in THRESHOLDS}
            for i in range(251, len(bars) - 20):
                date = bars[i]["date"]
                si, ei = date_to_index[cfg["benchmark"]].get(date), etf_lookup.get(date)
                if si is None or ei is None or i + 1 >= len(bars):
                    continue
                base, details = base_series[i]
                if base is None:
                    continue
                p = details["pillars"]
                p = {"trend": p["trend"], "relative_strength": p["relative_strength"], "contraction": p["contraction"], "setup": p["setup"]}
                scores = {"BASELINE": base, "WEIGHT_25_30_30_15": _score_variant(base, p, "WEIGHT_25_30_30_15", 0)}
                for model in models[2:]:
                    points = overlay_points(model, i, bars[i]["close"], feat)
                    scores[model] = _score_variant(base, p, model, points)
                for model, score in scores.items():
                    for threshold in THRESHOLDS:
                        key = (model, threshold)
                        crossed = score >= threshold and not previous[key]
                        previous[key] = score >= threshold
                        # 20-session cooldown prevents overlapping outcome windows.
                        if not crossed or i - last_signal[key] < 20:
                            continue
                        last_signal[key] = i
                        entry_i = i + 1
                        entry_etf_i = etf_lookup.get(bars[entry_i]["date"])
                        if entry_etf_i is None:
                            continue
                        for h in HORIZONS:
                            exit_i = i + h
                            if exit_i >= len(bars):
                                continue
                            exit_etf_i = etf_lookup.get(bars[exit_i]["date"])
                            if exit_etf_i is None:
                                continue
                            asset_ret = (bars[exit_i]["close"] / bars[entry_i]["open"] - 1) * 100 - cost_pct
                            etf_ret = (etf_bars[exit_etf_i]["close"] / etf_bars[entry_etf_i]["open"] - 1) * 100 - cost_pct
                            observations.append({"model": model, "threshold": threshold, "ticker": ticker,
                                "sector_etf": etf, "signal_date": date, "signal_year": int(date[:4]),
                                "entry_date": bars[entry_i]["date"], "exit_date": bars[exit_i]["date"],
                                "horizon": h, "score": round(score, 3), "return_pct": asset_ret,
                                "benchmark_pct": etf_ret, "excess_pct": asset_ret - etf_ret})
    return observations


def _aggregate(observations: list[dict]) -> dict:
    summary = {}
    for model in sorted({x["model"] for x in observations}):
        summary[model] = {"by_threshold": {}}
        for threshold in THRESHOLDS:
            summary[model]["by_threshold"][str(threshold)] = {"by_horizon": {}}
            for horizon in HORIZONS:
                rows = [x for x in observations if x["model"] == model and x["threshold"] == threshold and x["horizon"] == horizon]
                summary[model]["by_threshold"][str(threshold)]["by_horizon"][str(horizon)] = {
                    "all": _metrics(rows),
                    "annual": {str(year): _metrics([x for x in rows if x["signal_year"] == year]) for year in sorted({x["signal_year"] for x in rows})},
                    "by_sector": {sector: _metrics([x for x in rows if x["sector_etf"] == sector]) for sector in sorted({x["sector_etf"] for x in rows})},
                    "periods": {
                        "train_2022_2024": _metrics([x for x in rows if 2022 <= x["signal_year"] <= 2024]),
                        "validation_2025": _metrics([x for x in rows if x["signal_year"] == 2025]),
                        "oos_2026_to_latest": _metrics([x for x in rows if x["signal_year"] >= 2026]),
                    },
                }
    return summary


def _portfolio_backtest(observations: list[dict], bars_by_symbol: dict[str, list[dict]], max_positions: int = 20) -> dict:
    """Cash/share ledger, fixed initial-capital budgets, no borrowing.

    Benchmark trades mirror the chosen events and dollar budgets. A budget is
    reduced when either paired ledger has insufficient cash, including fees.
    Positions preserve their purchased quantities until their exit close.
    """
    result = {}
    asset_maps = {s: {b["date"]: b for b in rows} for s, rows in bars_by_symbol.items()}
    half_cost = ROUND_TRIP_COST_BPS / 20000
    for model in sorted({r["model"] for r in observations}):
        events = [r for r in observations if r["model"] == model and r["threshold"] == 65 and r["horizon"] == 20]
        by_entry = defaultdict(list)
        for event in events:
            by_entry[event["entry_date"]].append(event)
        if not events:
            continue
        date_range = sorted(set(b["date"] for b in bars_by_symbol.get("SPY", [])))
        start, end = min(by_entry), max(x["exit_date"] for x in events)
        date_range = [d for d in date_range if start <= d <= end]
        active = []
        nav = benchmark_nav = 1.0
        cash = benchmark_cash = 1.0
        asset_returns = []
        peak = 1.0
        max_dd = 0.0
        admitted = skipped = missing_marks = max_active = 0
        partial_funding = 0
        weight = 1.0 / max_positions
        for date in date_range:
            candidates = sorted(by_entry.get(date, []), key=lambda x: (-x["score"], x["ticker"]))
            slots = max(0, max_positions - len(active))
            chosen, skipped_today = candidates[:slots], candidates[slots:]
            skipped += len(skipped_today)
            for event in chosen:
                notional = max(0.0, min(weight, cash / (1 + half_cost), benchmark_cash / (1 + half_cost)))
                if notional < 1e-12:
                    skipped += 1
                    continue
                if notional < weight - 1e-12:
                    partial_funding += 1
                op = asset_maps[event["ticker"]][date]["open"]
                etf_op = asset_maps[event["sector_etf"]][date]["open"]
                active.append({**event, "shares":notional/op,"etf_shares":notional/etf_op,
                    "notional":notional,"mark":op,"etf_mark":etf_op})
                cash -= notional * (1 + half_cost)
                benchmark_cash -= notional * (1 + half_cost)
                admitted += 1
            max_active = max(max_active, len(active))
            survivors = []
            for pos in active:
                asset_bar = asset_maps[pos["ticker"]].get(date)
                etf_bar = asset_maps[pos["sector_etf"]].get(date)
                if asset_bar is None or etf_bar is None:
                    missing_marks += 1
                    survivors.append(pos)
                    continue
                pos["mark"] = asset_bar["close"]
                pos["etf_mark"] = etf_bar["close"]
                if date == pos["exit_date"]:
                    cash += pos["shares"] * pos["mark"] - pos["notional"] * half_cost
                    benchmark_cash += pos["etf_shares"] * pos["etf_mark"] - pos["notional"] * half_cost
                else:
                    survivors.append(pos)
            active = survivors
            new_nav = cash + sum(p["shares"] * p["mark"] for p in active)
            benchmark_nav = benchmark_cash + sum(p["etf_shares"] * p["etf_mark"] for p in active)
            asset_returns.append(new_nav / nav - 1 if nav else 0.0)
            nav = new_nav
            peak = max(peak, nav)
            max_dd = min(max_dd, nav / peak - 1)
        vol = statistics.pstdev(asset_returns) if len(asset_returns) > 1 else 0.0
        result[model] = {"signals_available": len(events), "positions_admitted": admitted,
            "signals_skipped_capacity": skipped, "max_concurrent_positions": max_active,
            "max_positions": max_positions, "fixed_weight_pct": round(100 * weight, 2),
            "portfolio_return_pct": round((nav - 1) * 100, 3),
            "sector_benchmark_return_pct": round((benchmark_nav - 1) * 100, 3),
            "excess_pct": round((nav - benchmark_nav) * 100, 3),
            "max_drawdown_pct": round(max_dd * 100, 3),
            "annualized_sharpe_approx": round(statistics.fmean(asset_returns) / vol * math.sqrt(252), 3) if vol else None,
            "missing_mark_observations": missing_marks,
            "partial_funding_trades": partial_funding,
            "accounting_version": "CASH_SHARES_LEDGER_V2",
            "assumptions": "Fixed initial-NAV notional budgets; cash/share ledger without borrowing; fee half at entry/exit; quantities preserved; paired asset/ETF cash may reduce budget; missing marks explicitly counted"}
    return result


def load_universe(as_of: str | None = None) -> tuple[dict[str, list[dict]], dict]:
    cfg = config()
    con = connect()
    symbols = {cfg["benchmark"], *cfg["sectors"].keys()}
    for tickers in cfg["sectors"].values():
        symbols.update(tickers)
    bars = {symbol: [bar for bar in load_bars(con, symbol) if as_of is None or bar["date"]<=as_of] for symbol in sorted(symbols)}
    con.close()
    return bars, cfg


def refresh() -> dict:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cutoff_file=ROOT/"config"/"regime_sensitivity.json"
    cutoff=json.loads(cutoff_file.read_text())["as_of"] if cutoff_file.exists() else None
    bars, cfg = load_universe(cutoff)
    observations = generate_observations(bars, cfg)
    result = {
        "status": "OK" if observations else "DATA UNAVAILABLE",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "as_of":cutoff,
        "correction_note":"Portfolio cash/share ledger V2 supersedes the previous sum-of-daily-percentages illustration. Current-day intraday observations are excluded by research cutoff.",
        "source": "Existing local SQLite OHLCV loaded from Yahoo Finance Chart API",
        "date_range": {s: {"first": rows[0]["date"], "last": rows[-1]["date"], "bars": len(rows)} for s, rows in bars.items() if rows},
        "universe": {"configured_sector_etfs": len(cfg["sectors"]), "configured_tickers": sum(len(x) for x in cfg["sectors"].values()), "survivorship_note": "Current configured constituents are applied retrospectively; delisted/removed constituents are absent."},
        "method": {"signal": "score crosses above threshold on completed close T0; enter asset and sector ETF at next common session open; exit at close after h sessions", "horizons_sessions": HORIZONS, "thresholds": THRESHOLDS, "cooldown_sessions": 20, "round_trip_cost_bps": ROUND_TRIP_COST_BPS, "cost_treatment": "10 bps round-trip deducted from both asset and benchmark series; excess is net asset less net ETF", "daily_vwap_caveat": "VWAP20 and pivot-anchored AVWAP use daily HLC3*volume, an approximation rather than intraday transaction VWAP", "pivot_confirmation": f"5 bars left and {PIVOT_RIGHT} right; values are available only on the confirmation bar"},
        "variants": {"BASELINE": "Current 30/25/20/25 four-pillar score", "WEIGHT_25_30_30_15": "Rescaled current pillar subscores to 25/30/30/15; no component formula changed", "RSI14": "Baseline plus bounded RSI14 point overlay", "MACD": "Baseline plus bounded MACD(12,26,9) histogram overlay", "VWAP_AVWAP": "Baseline plus daily VWAP20 and latest confirmed pivot-low AVWAP overlay", "PIVOT_SR": "Baseline plus latest confirmed 5/5 pivot support/resistance overlay", "COMBINED_CAPPED": "Sum of overlays capped at +/-8 points"},
        "overlay_rules": {"RSI14": {"+3": "RSI 50–65", "+1": "RSI 45–<50 or >65–72", "-3": "RSI <40 or >78", "0": "otherwise"}, "MACD": {"+3": "histogram >0 and rising", "+1": "histogram >0 and not rising", "-3": "histogram <0 and falling", "-1": "histogram <0 and not falling"}, "VWAP_AVWAP": {"+3": "close above both VWAP20 proxy and AVWAP", "+1": "above VWAP20 only", "-1": "above AVWAP only", "-3": "below both"}, "PIVOT_SR": {"+3": "close above last confirmed pivot high", "+2": "close from support to 3% above support", "-3": "close below last confirmed pivot low", "0": "otherwise"}, "COMBINED_CAPPED": "sum selected overlays, clipped to [-8,+8]"},
        "results": _aggregate(observations),
        "portfolio_illustration_score65_h20": _portfolio_backtest(observations, bars),
        "observation_row_count": len(observations),
        "unique_signal_count": len({(x["model"], x["threshold"], x["ticker"], x["signal_date"]) for x in observations}),
    }
    (OUT_DIR / "score_research.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    with (OUT_DIR / "observations.csv").open("w", encoding="utf-8-sig", newline="") as f:
        fields = ["model", "threshold", "ticker", "sector_etf", "signal_date", "entry_date", "exit_date", "horizon", "score", "return_pct", "benchmark_pct", "excess_pct"]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows({k: row[k] for k in fields} for row in observations)
    (OUT_DIR / "score_research_report.md").write_text(render_report(result), encoding="utf-8")
    return result


def render_report(result: dict) -> str:
    lines = ["# Investigación y backtest del score", "", f"Generado UTC: {result['generated_at_utc']}", "", f"Estado: **{result['status']}** · filas señal-horizonte: **{result['observation_row_count']}** · eventos únicos por variante/umbral: **{result['unique_signal_count']}**", "", "## Alcance y límites", "", "Estudio aislado: no cambia score, pesos, umbrales, señales ni backtests de producción. El universo actual tiene sesgo de supervivencia porque aplica los constituyentes configurados hoy a todo el histórico. Los precios son OHLCV diarios de Yahoo sin serie de retorno total ajustada por dividendos. Las métricas son descriptivas y no prueban una ventaja operable.", "", "## Método", "", "Señal: cruce del umbral al cierre confirmado T0; entrada a la apertura de la siguiente rueda común; salida al cierre en T+5/T+10/T+20. Cooldown fijo de 20 ruedas por ticker/variante/umbral evita ventanas superpuestas. Se deducen 10 pb de coste round-trip a la acción y al ETF; el exceso es retorno neto de la acción menos retorno neto del ETF. Definiciones prefijadas, sin selección de parámetros según resultados.", "", "## Retorno a 20 ruedas, score ≥65", "", "| Variante | N | Win % | Retorno medio | Mediana | ETF medio | Exceso medio | Exceso win % |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for model, values in result.get("results", {}).items():
        m = values["by_threshold"]["65"]["by_horizon"]["20"]["all"]
        if m:
            lines.append(f"| {model} | {m['signals']} | {m['win_rate_pct']:.2f}% | {m['mean_return_pct']:.3f}% | {m['median_return_pct']:.3f}% | {m['mean_benchmark_pct']:.3f}% | {m['mean_excess_pct']:.3f}% | {m['excess_win_rate_pct']:.2f}% |")
    base_all = result["results"]["BASELINE"]["by_threshold"]["65"]["by_horizon"]["20"]["all"]
    base_oos = result["results"]["BASELINE"]["by_threshold"]["65"]["by_horizon"]["20"]["periods"]["oos_2026_to_latest"]
    combo_oos = result["results"]["COMBINED_CAPPED"]["by_threshold"]["65"]["by_horizon"]["20"]["periods"]["oos_2026_to_latest"]
    lines += ["", "## Conclusión provisional", "", f"El baseline obtiene {base_all['win_rate_pct']:.2f}% de win rate y {base_all['mean_excess_pct']:.3f}% de exceso medio a 20 ruedas en todo el período. En el corte temporal 2026, el baseline marca {base_oos['win_rate_pct']:.2f}% de win rate y {base_oos['mean_excess_pct']:.3f}% de exceso; el combinado acotado marca {combo_oos['win_rate_pct']:.2f}% y {combo_oos['mean_excess_pct']:.3f}%. Esto convierte al combinado en candidato para otra validación, no en un ganador validado: el test 2026 es un solo régimen anual parcial y se probaron varias hipótesis.", "", "Los pesos 25/30/30/15 no mejoran el baseline de forma consistente y no se recomienda sustituir los pesos actuales con esta evidencia. Los overlays individuales tampoco superan al ETF en exceso medio en el corte 2026. No se recomienda elevar el umbral automáticamente: en el baseline el 75 mejora el exceso 2026 sólo modestamente y el 80 tiene poca muestra y baja tasa de aciertos."]
    lines += ["", "## Walk-forward calendar slices (20 sessions, threshold 65)", "", "Fixed slices: signals from 2022–2024 (development), 2025 (validation), and 2026 through the latest available date (out of sample by time). No model or threshold was selected from those outputs. Small sample sizes must be read alongside every metric.", "", "| Variante | Período | N | Win % | Retorno medio | Exceso medio |", "|---|---|---:|---:|---:|---:|"]
    for model, values in result.get("results", {}).items():
        periods = values["by_threshold"]["65"]["by_horizon"]["20"]["periods"]
        for name, m in periods.items():
            if m:
                lines.append(f"| {model} | {name} | {m['signals']} | {m['win_rate_pct']:.2f}% | {m['mean_return_pct']:.3f}% | {m['mean_excess_pct']:.3f}% |")
    lines += ["", "Anualizado para las dos variantes de referencia:", "", "| Variante | Año | N | Win % | Retorno medio | Exceso medio |", "|---|---:|---:|---:|---:|---:|"]
    for model in ("BASELINE", "COMBINED_CAPPED"):
        annual = result["results"][model]["by_threshold"]["65"]["by_horizon"]["20"]["annual"]
        for year, m in annual.items():
            lines.append(f"| {model} | {year} | {m['signals']} | {m['win_rate_pct']:.2f}% | {m['mean_return_pct']:.3f}% | {m['mean_excess_pct']:.3f}% |")
    lines += ["", "## Cartera ilustrativa corregida, score ≥65 y 20 ruedas", "", "CORRECCIÓN: los resultados previos de cartera sumaban porcentajes diarios con ponderación fija y no conservaban las cantidades compradas; quedan reemplazados por este ledger de cash y acciones. Estadísticas por evento son un cálculo independiente. Corte conservador excluye la barra del día13 obtenida antes de su cierre.", "", "Hasta20 posiciones; presupuesto nominal máximo5% del capital inicial, reducido si el cash de la pareja acción/ETF no alcanza incluyendo gastos. Cantidades constantes durante cada trade; NAV=cash+valor de posiciones. Coste10pb dividido entre entrada y salida. Señales simultáneas ordenadas por score, cash sin interés, sin financiación ni préstamo. La restricción de cash emparejado es una convención ilustrativa, no una estrategia operativa.", "", "| Variante | Entradas | Admitidas | Omitidas por capacidad | Retorno | ETF de referencia | Exceso | Max drawdown | Sharpe aprox. |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for model, p in result.get("portfolio_illustration_score65_h20", {}).items():
        sharpe = "DATA UNAVAILABLE" if p["annualized_sharpe_approx"] is None else f"{p['annualized_sharpe_approx']:.2f}"
        lines.append(f"| {model} | {p['signals_available']} | {p['positions_admitted']} | {p['signals_skipped_capacity']} | {p['portfolio_return_pct']:.2f}% | {p['sector_benchmark_return_pct']:.2f}% | {p['excess_pct']:.2f}% | {p['max_drawdown_pct']:.2f}% | {sharpe} |")
    lines += ["", "## Sensibilidad de umbral (20 ruedas)", "", "Cambiar umbral altera a la vez el número y la composición de señales; la tabla no estima el efecto aislado de una fórmula.", "", "| Variante | Umbral | N | Win % | Retorno medio | Exceso medio |", "|---|---:|---:|---:|---:|---:|"]
    for model, values in result.get("results", {}).items():
        for threshold in THRESHOLDS:
            m = values["by_threshold"][str(threshold)]["by_horizon"]["20"]["all"]
            if m:
                lines.append(f"| {model} | {threshold} | {m['signals']} | {m['win_rate_pct']:.2f}% | {m['mean_return_pct']:.3f}% | {m['mean_excess_pct']:.3f}% |")
    baseline_sectors = result["results"].get("BASELINE", {}).get("by_threshold", {}).get("65", {}).get("by_horizon", {}).get("20", {}).get("by_sector", {})
    sector_rows = [(sector, metrics) for sector, metrics in baseline_sectors.items() if metrics]
    sector_rows.sort(key=lambda item: item[1]["mean_excess_pct"])
    lines += ["", "## Sectores del baseline", "", "Sectores con menor y mayor exceso medio, 20 ruedas y score 65. Son observaciones agrupadas; no corrigen el sesgo de supervivencia ni la concentración de eventos.", "", "| ETF | N | Win % | Retorno medio | Exceso medio |", "|---|---:|---:|---:|---:|"]
    selected_sectors = sector_rows[:4] + sector_rows[-4:] if len(sector_rows) > 8 else sector_rows
    for sector, m in selected_sectors:
        lines.append(f"| {sector} | {m['signals']} | {m['win_rate_pct']:.2f}% | {m['mean_return_pct']:.3f}% | {m['mean_excess_pct']:.3f}% |")
    ranges = list(result.get("date_range", {}).values())
    firsts, lasts = sorted(x["first"] for x in ranges), sorted(x["last"] for x in ranges)
    spy = result["date_range"].get("SPY", {})
    lines += ["", "## Reglas de overlays probados", "", "- **RSI14:** +3 entre 50–65; +1 entre 45–<50 o >65–72; −3 debajo de 40 o arriba de 78; 0 en el resto.", "- **MACD(12,26,9):** +3 con histograma positivo y creciente; +1 positivo sin crecer; −3 negativo y descendente; −1 negativo sin descender.", "- **VWAP/AVWAP:** +3 por encima de ambas; +1 sólo sobre VWAP20; −1 sólo sobre AVWAP; −3 debajo de ambas. VWAP20 usa HLC3 y volumen diario; AVWAP inicia desde el último pivot low confirmado.", "- **Pivotes:** +3 cierre arriba del último pivot high confirmado; +2 desde soporte hasta 3% sobre soporte; −3 bajo el último pivot low; 0 en otro caso. Confirmación 5 barras a izquierda y 5 a derecha.", "- **Combinado:** suma de overlays con tope ±8; puntos agregados al score base y truncados a 0–99,5.", "", "RSI y MACD comparten información de momentum con EMA/tendencia; los overlays pueden producir más cruces de umbral. VWAP diario no equivale a VWAP intradía. Los pivotes no se usan retrospectivamente antes de confirmarse.", "", "## Cobertura", "", f"{len(ranges)} símbolos; rango global {firsts[0]}–{lasts[-1]}. SPY: {spy.get('first')}–{spy.get('last')} ({spy.get('bars')} ruedas). Símbolos con IPO/altas posteriores o sesiones ausentes usan sus datos observados y fechas comunes; no se imputan.", "", "Limitación central: el período OOS disponible aquí sigue siendo corto y pertenece a un único régimen anual reciente. Para promover una variante hacen falta más años, constituyentes históricos y costes realistas por liquidez.", "", "Detalle de cobertura por símbolo y resultados completos: `score_research.json`. Eventos auditables: `observations.csv`.", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    output = refresh()
    print(f"Score research {output['status']} · {output['observation_row_count']} observation rows · {OUT_DIR}")
