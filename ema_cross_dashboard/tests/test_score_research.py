import unittest

from app.engine import four_pillar_score
from app.score_research import _portfolio_backtest, _score_variant, baseline_score_series, indicators, overlay_points


def make_bars(n=180):
    rows = []
    for i in range(n):
        close = 100 + i * 0.17 + (3 if i % 9 < 4 else -1)
        rows.append({"date": f"2025-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}",
                     "open": close - 0.2, "high": close + 1.1,
                     "low": close - 1.2, "close": close,
                     "volume": 1000 + (i % 13) * 25})
    return rows


class ScoreResearchTests(unittest.TestCase):
    def test_features_are_prefix_stable(self):
        bars = make_bars()
        full = indicators(bars)
        cut = 130
        prefix = indicators(bars[:cut])
        for name in full:
            self.assertEqual(full[name][:cut], prefix[name])

    def test_pivot_is_only_available_after_right_confirmation(self):
        bars = make_bars(80)
        bars[30]["low"] = 70
        for i in range(26, 36):
            if i != 30:
                bars[i]["low"] = 80 + (i % 3)
        feat = indicators(bars)
        self.assertIsNone(feat["pivot_support"][34])
        self.assertEqual(feat["pivot_support"][35], 70)

    def test_overlay_score_is_bounded_and_weight_variant_rescales(self):
        pillars = {"trend": 30.0, "relative_strength": 25.0, "contraction": 20.0, "setup": 25.0}
        self.assertEqual(_score_variant(99.5, pillars, "WEIGHT_25_30_30_15", 0), 99.5)
        self.assertEqual(_score_variant(90, pillars, "RSI14", 15), 99.5)
        self.assertEqual(_score_variant(10, pillars, "MACD", -15), 0)

    def test_overlay_uses_confirmed_features_at_current_bar(self):
        feat = indicators(make_bars())
        i = 150
        self.assertIn(overlay_points("RSI14", i, make_bars()[i]["close"], feat), (-3, 0, 1, 3))
        self.assertIn(overlay_points("MACD", i, make_bars()[i]["close"], feat), (-3, -1, 0, 1, 3))

    def test_fast_baseline_matches_production_score(self):
        bars = make_bars(310)
        spy = [{**bar, "close": bar["close"] * 0.7} for bar in bars]
        sector = [{**bar, "close": bar["close"] * 1.1} for bar in bars]
        series = baseline_score_series(bars, spy, sector)
        for i in (200, 224, 260, 309):
            expected, detail = four_pillar_score(bars[:i + 1], [x["close"] for x in spy[:i + 1]], [x["close"] for x in sector[:i + 1]])
            self.assertEqual(series[i][0], expected)
            self.assertEqual(series[i][1]["pillars"], detail["pillars"])

    def test_baseline_alignment_preserves_full_benchmark_calendar_on_missing_asset_day(self):
        full = make_bars(310)
        spy = [{**bar, "close": bar["close"] * 0.7} for bar in full]
        sector = [{**bar, "close": bar["close"] * 1.1} for bar in full]
        asset = full[:245] + full[246:]
        series = baseline_score_series(asset, spy, sector)
        i = 260
        date = asset[i]["date"]
        expected, _ = four_pillar_score(asset[:i + 1], [x["close"] for x in spy if x["date"] <= date], [x["close"] for x in sector if x["date"] <= date])
        self.assertEqual(series[i][0], expected)

    def test_capacity_portfolio_respects_position_limit(self):
        bars = make_bars(30)
        data = {"AAA": bars, "XLK": bars, "BBB": bars, "SPY": bars}
        events = []
        for ticker in ("AAA", "BBB"):
            events.append({"model": "BASELINE", "threshold": 65, "horizon": 20,
                           "ticker": ticker, "sector_etf": "XLK", "entry_date": bars[1]["date"],
                           "exit_date": bars[21]["date"], "score": 80, "return_pct": 1.0})
        output = _portfolio_backtest(events, data, max_positions=1)["BASELINE"]
        self.assertEqual(output["positions_admitted"], 1)
        self.assertEqual(output["signals_skipped_capacity"], 1)
        self.assertEqual(output["max_concurrent_positions"], 1)

    def test_portfolio_preserves_shares_instead_of_summing_daily_percentages(self):
        rows=[{"date":"2025-01-02","open":100,"close":110}, {"date":"2025-01-03","open":110,"close":121}]
        data={"AAA":rows,"SPY":rows,"XLK":rows}
        event={"model":"BASELINE","threshold":65,"horizon":20,"ticker":"AAA","sector_etf":"XLK","entry_date":"2025-01-02","exit_date":"2025-01-03","score":80}
        result=_portfolio_backtest([event],data,max_positions=1)["BASELINE"]
        expected=(1.21-.0005)/1.0005-1
        self.assertAlmostEqual(result["portfolio_return_pct"],expected*100,places=3)
        self.assertEqual(result["accounting_version"],"CASH_SHARES_LEDGER_V2")


if __name__ == "__main__":
    unittest.main()
