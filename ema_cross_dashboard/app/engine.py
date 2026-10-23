from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from .market import ema, fmt, pct_change, percentile_rank, safe_div, sma, wma

# Presentation-only prioritisation.  The historical score, its state and the
# backtest keep their original definitions.  A separate score lets the daily
# screener deprioritise two conditions the user explicitly does not want to
# chase, while preserving the underlying signal for review.
PRESENTATION_PENALTIES={'WEAKENING':12,'EXTENDED / DO NOT CHASE':15}
OPERATIONAL_SCORE_THRESHOLD = 65
GATE_BREADTH_THRESHOLD = 50
GATE_VIX_MAX = 22
GATE_PUT_CALL_MAX = 1.05


def _atr_series(bars: list[dict], length: int = 14) -> list[float | None]:
    """Causal simple-TR ATR series, matching the approved research method."""
    if not bars:
        return []
    true_ranges: list[float | None] = [None]
    for prior, current in zip(bars[:-1], bars[1:]):
        true_ranges.append(max(current['high'] - current['low'], abs(current['high'] - prior['close']), abs(current['low'] - prior['close'])))
    result: list[float | None] = [None] * len(bars)
    for index in range(length, len(bars)):
        sample = true_ranges[index - length + 1:index + 1]
        if all(value is not None for value in sample):
            result[index] = sum(sample) / length
    return result


def selective_setup_score(bars: list[dict], spy_bars: list[dict]) -> dict:
    """Latest approved 25/30/30/15 research score, for monitor labelling only.

    It deliberately has no impact on the active four-pillar score.  All tests
    use completed bars only.  The pivot is confirmed with five bars to either
    side before it can become resistance, so the current bar never knows a
    future high.
    """
    if len(bars) < 252 or len(spy_bars) < 252:
        return {'score': None, 'setup': False, 'reason': 'INSUFFICIENT_HISTORY'}
    spy_by_date = {bar['date']: bar['close'] for bar in spy_bars}
    aligned = [bar for bar in bars if bar['date'] in spy_by_date]
    if len(aligned) < 252:
        return {'score': None, 'setup': False, 'reason': 'UNALIGNED_BENCHMARK'}
    closes = [bar['close'] for bar in aligned]
    opens = [bar['open'] for bar in aligned]
    highs = [bar['high'] for bar in aligned]
    lows = [bar['low'] for bar in aligned]
    volumes = [bar['volume'] for bar in aligned]
    spy = [spy_by_date[bar['date']] for bar in aligned]
    e21 = _ema_series(closes, 21)
    e200 = _ema_series(closes, 200)
    s50 = _sma_series(closes, 50)
    s200 = _sma_series(closes, 200)
    atr = _atr_series(aligned, 14)
    vol50 = _sma_series(volumes, 50)
    last = len(aligned) - 1
    values = (e21[last], e200[last], s50[last], s200[last], atr[last], vol50[last])
    if any(value is None or value <= 0 for value in values) or s200[last - 20] is None:
        return {'score': None, 'setup': False, 'reason': 'MISSING_FEATURES'}

    # Trend: 15 aligned, 5 positive SMA200 slope, 5 recent confirmed EMA200 event.
    aligned_trend = closes[last] > e21[last] > s50[last] > s200[last]
    positive_slope = s200[last] > s200[last - 20]
    ema200_event = False
    for index in range(max(200, last - 4), last + 1):
        if e200[index] is None or e200[index - 1] is None:
            continue
        crossed = closes[index] > e200[index] and closes[index - 1] <= e200[index - 1]
        rebound = lows[index] <= e200[index] and closes[index] > e200[index] and closes[index - 1] >= e200[index - 1]
        ema200_event = ema200_event or crossed or rebound
    trend_points = 15 * int(aligned_trend) + 5 * int(positive_slope) + 5 * int(ema200_event)

    ratios = [close / benchmark for close, benchmark in zip(closes, spy)]
    rs_ratio_series: list[float | None] = []
    for index, ratio in enumerate(ratios):
        baseline = sma(ratios[:index + 1], 50)
        rs_ratio_series.append(100 * ratio / baseline if baseline else None)
    rs_ratio = rs_ratio_series[last]
    momentum_base = sma([value for value in rs_ratio_series[-10:] if value is not None], 10)
    rs_momentum = 100 * rs_ratio / momentum_base if rs_ratio is not None and momentum_base else None
    if rs_ratio is None or rs_momentum is None:
        return {'score': None, 'setup': False, 'reason': 'MISSING_RS'}
    rs_points = 10 * int(rs_ratio > 100) + 10 * int(rs_momentum > 100) + 10 * int(rs_momentum > 102)

    atr_pct = [safe_div(item, close) for item, close in zip(atr, closes)]
    compressed = atr_pct[last] is not None and all(value is not None for value in atr_pct[-20:]) and atr_pct[last] <= min(atr_pct[-20:])
    dry = volumes[last] < .7 * vol50[last]
    vcp_points = 15 * int(compressed) + 15 * int(dry)

    # A base must be observed before the breakout bar.  Confirm pivots after
    # five bars on the right; only then is the resistance available.
    base = []
    for index in range(len(aligned)):
        window = atr_pct[max(0, index - 19):index + 1]
        base.append(bool(window and atr_pct[index] is not None and all(value is not None for value in window) and atr_pct[index] <= min(window) and vol50[index] is not None and volumes[index] < .7 * vol50[index]))
    prior_base = any(base[max(0, last - 20):last])
    resistance = None
    for pivot in range(5, last - 4):
        if highs[pivot] == max(highs[pivot - 5:pivot + 6]):
            resistance = highs[pivot]
    episodic = opens[last] / closes[last - 1] - 1 > .08 and volumes[last] > 2 * vol50[last]
    breakout = bool(resistance and closes[last] > resistance and closes[last - 1] <= resistance and prior_base)
    setup = episodic or breakout
    setup_points = 15 * int(setup)
    climax_penalty = 5 if closes[last] - s50[last] > 3.5 * atr[last] else 0
    score = max(0, min(100, trend_points + rs_points + vcp_points + setup_points - climax_penalty))
    return {
        'score': round(score, 2), 'setup': setup,
        'setup_reason': 'EPISODIC PIVOT' if episodic else 'VCP PIVOT BREAKOUT' if breakout else None,
        'trend_points': trend_points, 'rs_points': rs_points, 'vcp_points': vcp_points,
        'setup_points': setup_points, 'climax_penalty': climax_penalty,
        'rs_ratio': round(rs_ratio, 4), 'rs_momentum': round(rs_momentum, 4),
    }


def operational_gate(spy_bars: list[dict], qqq_bars: list[dict], vix_bars: list[dict], assets: list[dict], put_call_by_date: dict[str, float] | None = None) -> dict:
    """Evaluate the approved binary gate without silently filling missing data."""
    put_call_by_date = put_call_by_date or {}
    if len(spy_bars) < 200 or len(qqq_bars) < 200 or not vix_bars:
        return {'status': 'DATA UNAVAILABLE', 'eligible_for_new_entries': False, 'reasons': ['Market history insufficient']}
    as_of = spy_bars[-1]['date']
    qqq_close_by_date = {bar['date']: bar['close'] for bar in qqq_bars}
    vix_by_date = {bar['date']: bar['close'] for bar in vix_bars}
    qqq_index = next((index for index in range(len(qqq_bars) - 1, -1, -1) if qqq_bars[index]['date'] == as_of), None)
    vix = vix_by_date.get(as_of)
    if qqq_index is None or vix is None:
        return {'status': 'DATA UNAVAILABLE', 'eligible_for_new_entries': False, 'as_of': as_of, 'reasons': ['QQQ or VIX unavailable on SPY session']}
    spy_close = [bar['close'] for bar in spy_bars]
    qqq_close = [bar['close'] for bar in qqq_bars[:qqq_index + 1]]
    spy_trend = bool(spy_close[-1] > sma(spy_close, 50) > sma(spy_close, 200))
    qqq_trend = bool(qqq_close[-1] > sma(qqq_close, 50) > sma(qqq_close, 200))
    breadth_valid = [asset for asset in assets if asset.get('sma50') is not None]
    breadth = 100 * sum(asset['close'] > asset['sma50'] for asset in breadth_valid) / len(breadth_valid) if breadth_valid else None
    breadth_pass = breadth is not None and breadth > GATE_BREADTH_THRESHOLD
    vix_pass = vix < GATE_VIX_MAX
    source_date = spy_bars[-2]['date']
    put_call = put_call_by_date.get(source_date)
    put_call_pass = put_call is not None and put_call < GATE_PUT_CALL_MAX
    # Breadth is a contextual warning in the live monitor.  It remains visible
    # and parameterised at 50%, but it does not veto an otherwise valid new
    # entry. Historical gate research is preserved separately.
    available = put_call is not None
    full = available and spy_trend and qqq_trend and vix_pass and put_call_pass
    reasons = []
    warnings = []
    if not spy_trend: reasons.append('SPY trend')
    if not qqq_trend: reasons.append('QQQ trend')
    if not vix_pass: reasons.append('VIX')
    if put_call is None: reasons.append('Put/Call unavailable')
    elif not put_call_pass: reasons.append('Put/Call')
    if breadth is None: warnings.append('Breadth unavailable')
    elif not breadth_pass: warnings.append(f'Breadth below {GATE_BREADTH_THRESHOLD}%')
    return {
        'status': 'FULL' if full else 'DATA UNAVAILABLE' if not available else 'BLOCKED',
        'eligible_for_new_entries': full, 'as_of': as_of,
        'spy_trend': spy_trend, 'qqq_trend': qqq_trend,
        'breadth_pct': fmt(breadth, 1), 'breadth_valid_count': len(breadth_valid),
        'breadth_pass': breadth_pass, 'breadth_is_blocking': False,
        'vix': fmt(vix), 'vix_pass': vix_pass,
        'put_call': fmt(put_call, 3), 'put_call_source_date': source_date, 'put_call_pass': put_call_pass,
        'reasons': reasons, 'warnings': warnings,
    }


def sector_state(relative: float | None, breadth: float | None, acceleration: float | None) -> str:
    """Classify a sector with the same rules in the map and its table.

    Relative strength is the horizontal map axis and acceleration is the
    vertical axis.  A sector on the right but falling on the vertical axis is
    weakening, even when its current breadth remains high.
    """
    relative = relative or 0
    breadth = breadth or 0
    acceleration = acceleration or 0
    if relative > 0 and acceleration >= 0:
        return 'LEADING' if breadth >= 60 else 'IMPROVING'
    if relative > 0 and acceleration < 0:
        return 'WEAKENING'
    if relative <= 0 and acceleration >= 0:
        return 'RECOVERING'
    return 'LAGGING'


def _ema_series(values: list[float], length: int) -> list[float | None]:
    """Return a causal EMA series.  No point includes a later close."""
    result: list[float | None] = [None] * len(values)
    if len(values) < length:
        return result
    value = sum(values[:length]) / length
    result[length - 1] = value
    alpha = 2 / (length + 1)
    for index in range(length, len(values)):
        value += alpha * (values[index] - value)
        result[index] = value
    return result


def _trend_window(bars: list[dict], sessions: int = 60) -> list[dict]:
    """Compact causal close/EMA series used only by the asset detail chart."""
    closes = [bar['close'] for bar in bars]
    fast, slow = _ema_series(closes, 9), _ema_series(closes, 21)
    mid, long = _sma_series(closes, 50), _ema_series(closes, 200)
    start = max(0, len(bars) - sessions)
    return [
        {
            'date': bar['date'], 'close': fmt(bar['close']), 'ema9': fmt(fast[index]),
            'ema21': fmt(slow[index]), 'sma50': fmt(mid[index]), 'ema200': fmt(long[index]),
        }
        for index, bar in enumerate(bars[start:], start)
    ]


def _sma_series(values: list[float], length: int) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    if len(values) < length:
        return result
    total = sum(values[:length])
    result[length - 1] = total / length
    for index in range(length, len(values)):
        total += values[index] - values[index - length]
        result[index] = total / length
    return result


def independent_detectors(bars: list[dict]) -> list[dict]:
    """Return transparent, non-scoring pattern observations at the latest close.

    These are deliberately separate from the four-pillar score.  Every rule
    works from completed daily bars through the current index only.
    """
    if len(bars) < 201:
        return []
    closes = [bar['close'] for bar in bars]
    lows = [bar['low'] for bar in bars]
    volumes = [bar['volume'] for bar in bars]
    ema200 = _ema_series(closes, 200)
    volume50 = _sma_series(volumes, 50)
    detections: list[dict] = []
    last = len(bars) - 1

    daily_return = pct_change(closes, 1)
    if daily_return is not None and volume50[last] and daily_return >= .04 and volumes[last] >= 2 * volume50[last]:
        detections.append({'name': 'EPISODIC PIVOT', 'date': bars[last]['date'], 'detail': 'subida diaria ≥4% y volumen ≥2.0x SMA50'})

    # Scan a short completed-bar window so a recent reclaim/cross remains
    # visible without promoting it to a persistent score component.
    ema_events: list[tuple[int, str]] = []
    for index in range(max(200, last - 5), last + 1):
        current, prior = ema200[index], ema200[index - 1]
        if current is None or prior is None:
            continue
        confirmed_volume = volume50[index] is not None and volumes[index] >= .8 * volume50[index]
        if closes[index] > current and closes[index - 1] <= prior and confirmed_volume:
            ema_events.append((index, 'EMA200 CROSS UP'))
        elif closes[index] < current and closes[index - 1] >= prior:
            ema_events.append((index, 'EMA200 CROSS DOWN'))
        elif lows[index] <= current * 1.01 and closes[index] > current and confirmed_volume:
            ema_events.append((index, 'EMA200 RECLAIM'))
    if ema_events:
        index, name = ema_events[-1]
        detections.append({'name': name, 'date': bars[index]['date'], 'detail': 'contacto/corte EMA200 confirmado al cierre'})

    # VCP candidate: three successively smaller price ranges, contracting
    # average volume, price above SMA50 and close in the upper part of range.
    sma50 = _sma_series(closes, 50)
    if last >= 60 and sma50[last] and volume50[last]:
        ranges = [
            safe_div(max(closes[-60:]) - min(closes[-60:]), max(closes[-60:])),
            safe_div(max(closes[-40:]) - min(closes[-40:]), max(closes[-40:])),
            safe_div(max(closes[-20:]) - min(closes[-20:]), max(closes[-20:])),
        ]
        volume10 = sma(volumes, 10)
        upper_range = safe_div(closes[-1] - min(closes[-20:]), max(closes[-20:]) - min(closes[-20:]))
        if None not in ranges and volume10 is not None and upper_range is not None and ranges[2] < ranges[1] < ranges[0] and volume10 < volume50[last] and closes[-1] > sma50[last] and upper_range >= .65:
            detections.append({'name': 'VCP CANDIDATE', 'date': bars[last]['date'], 'detail': 'rangos 60/40/20d decrecientes y volumen 10d < SMA50'})
    return detections


def sessions_volume_metrics(closes: list[float], volumes: list[float]) -> dict:
    """Participation proxy for the last ten completed sessions.

    It is an analytical ranking only: count positive sessions and weight that
    count by average ten-day volume versus the 50-day baseline.
    """
    if len(closes) < 51 or len(volumes) < 50:
        return {'up_sessions_10': None, 'volume_ratio10_50': None, 'sessions_volume_score': None}
    up_sessions = sum(closes[index] > closes[index - 1] for index in range(len(closes) - 10, len(closes)))
    ratio = safe_div(sma(volumes, 10), sma(volumes, 50))
    return {
        'up_sessions_10': up_sessions,
        'volume_ratio10_50': fmt(ratio),
        'sessions_volume_score': fmt(up_sessions * ratio if ratio is not None else None),
    }


def _stage(bars: list[dict]) -> tuple[str, float | None]:
    closes=[bar['close'] for bar in bars]; ema200=_ema_series(closes,200)
    current=ema200[-1]
    if current is None or len(ema200) < 221 or ema200[-21] is None:
        return 'DATA UNAVAILABLE', None
    slope=safe_div(current,ema200[-21])
    slope_pct=(slope-1)*100 if slope is not None else None
    if closes[-1] >= current and (slope_pct or 0) >= 0: return 'STAGE 2', slope_pct
    if closes[-1] >= current: return 'STAGE 3', slope_pct
    if (slope_pct or 0) >= 0: return 'STAGE 1', slope_pct
    return 'STAGE 4', slope_pct


def _setup_freshness(bars: list[dict], window: int = 20) -> dict:
    """Find the latest confirmed cross, reclaim or breakout using only past bars."""
    closes=[bar['close'] for bar in bars]; fast=_ema_series(closes,9); slow=_ema_series(closes,21); sma50=_sma_series(closes,50)
    events=[]; start=max(50,len(bars)-window)
    for index in range(start,len(bars)):
        if None in (fast[index],slow[index],fast[index-1],slow[index-1],sma50[index],sma50[index-1]):
            continue
        event=None
        if fast[index]>slow[index] and fast[index-1]<=slow[index-1]: event='EMA9/21 CROSS'
        elif closes[index]>sma50[index] and closes[index-1]<=sma50[index-1]: event='SMA50 RECLAIM'
        elif index>=21 and closes[index]>max(closes[index-20:index]) and closes[index-1]<=max(closes[index-21:index-1]): event='20D BREAKOUT'
        if event: events.append((index,event))
    if not events: return {'event':None,'age_sessions':None,'fresh':False}
    index,event=events[-1]
    age=len(bars)-1-index
    return {'event':event,'age_sessions':age,'fresh':age<=5}


def _rs_composite(closes: list[float], spy: list[float]) -> float | None:
    """Multi-horizon relative return; later converted to a universe percentile."""
    if len(closes)<121 or len(spy)<121: return None
    weighted=[]
    for sessions,weight in ((20,.50),(60,.30),(120,.20)):
        stock_return=pct_change(closes,sessions); spy_return=pct_change(spy,sessions)
        if stock_return is None or spy_return is None: return None
        weighted.append((stock_return-spy_return)*weight)
    return sum(weighted)


def _add_rs_percentiles(assets: list[dict]) -> None:
    ranked=sorted((asset for asset in assets if asset['rs_composite_raw'] is not None),key=lambda asset:asset['rs_composite_raw'])
    if not ranked: return
    denominator=max(1,len(ranked)-1)
    for index,asset in enumerate(ranked): asset['rs_score']=round(100*index/denominator,1)


def _atr(bars: list[dict], length: int = 14) -> float | None:
    if len(bars) <= length: return None
    true_ranges = []
    for prior, current in zip(bars[-length-1:-1], bars[-length:]):
        true_ranges.append(max(current['high']-current['low'], abs(current['high']-prior['close']), abs(current['low']-prior['close'])))
    return sum(true_ranges) / length


def _clip(value: float | None, lower: float = 0, upper: float = 1) -> float:
    if value is None:
        return lower
    return max(lower, min(upper, value))


def _score(closes: list[float], volumes: list[float], spy: list[float], sector: list[float]) -> tuple[float | None, dict]:
    e9,e21,e200=ema(closes,9),ema(closes,21),ema(closes,200)
    s50=sma(closes,50); vol50=sma(volumes,50)
    if None in (e9,e21,e200,s50,vol50) or len(spy)<21 or len(sector)<21: return None, {}
    trend = 25 if closes[-1] > e200 else 0
    cross = 20 if e9 > e21 else 0
    structure = 15 if closes[-1] > s50 else 0
    stock_rs = pct_change(closes,20); spy_return=pct_change(spy,20); sector_return=pct_change(sector,20)
    relative = 20 if stock_rs is not None and spy_return is not None and stock_rs > spy_return else 0
    sector_rs = 10 if sector_return is not None and spy_return is not None and sector_return > spy_return else 0
    volume = 10 if volumes[-1] > vol50 else 0
    return trend+cross+structure+relative+sector_rs+volume, {
        'ema9': e9, 'ema21': e21, 'ema200': e200, 'sma50': s50, 'volume_sma50': vol50,
        'stock_return20': stock_rs, 'sector_return20': sector_return,
        'relative_strength': None if stock_rs is None or spy_return is None else stock_rs-spy_return,
    }


def four_pillar_score(bars: list[dict], spy: list[float], sector: list[float]) -> tuple[float | None, dict]:
    """Continuous 0--99.5 diagnostic score with four explainable pillars.

    This intentionally avoids fitting weights to realised returns.  Each pillar
    describes a distinct observable condition: durable trend, relative strength,
    volatility/volume contraction and entry setup quality.  The 99.5 ceiling
    communicates that a screen never provides certainty.
    """
    closes=[bar['close'] for bar in bars]; volumes=[bar['volume'] for bar in bars]
    e9,e21,e200=ema(closes,9),ema(closes,21),ema(closes,200)
    s50=sma(closes,50); vol10=sma(volumes,10); vol50=sma(volumes,50)
    atr14,atr50=_atr(bars,14),_atr(bars,50)
    if None in (e9,e21,e200,s50,vol10,vol50,atr14,atr50) or len(spy)<61 or len(sector)<21:
        return None, {}
    ema21_series=_ema_series(closes,21)
    slope21=safe_div(e21,ema21_series[-21]) - 1 if ema21_series[-21] is not None else None
    stock20,spy20,sector20=pct_change(closes,20),pct_change(spy,20),pct_change(sector,20)
    stock60,spy60=pct_change(closes,60),pct_change(spy,60)
    rel20=(stock20-spy20) if stock20 is not None and spy20 is not None else None
    rel60=(stock60-spy60) if stock60 is not None and spy60 is not None else None
    sector_rel=(sector20-spy20) if sector20 is not None and spy20 is not None else None
    atr_ratio=safe_div(atr14,atr50)
    range20=safe_div(max(closes[-20:])-min(closes[-20:]),closes[-1])
    volume_contraction=safe_div(vol10,vol50)
    volume_ratio=safe_div(volumes[-1],vol50)
    gap_fast=safe_div(e9-e21,atr14)
    gap_mid=safe_div(e21-s50,atr14)
    extension=safe_div(closes[-1]-e21,atr14)
    near_high=safe_div(closes[-1],max(closes[-20:]))

    trend=(10*_clip(safe_div(closes[-1]/e200-.97,.10)) +
           4*_clip(safe_div(gap_fast,1.5)) +
           4*_clip(safe_div(gap_mid,3)) +
           7*_clip(safe_div(slope21,.05)) +
           5*_clip(safe_div(closes[-1]/s50-.98,.06)))
    relative_strength=(12*_clip(safe_div(rel20,.12)) +
                       8*_clip(safe_div(rel60,.20)) +
                       5*_clip(safe_div(sector_rel,.10)))
    contraction=(8*_clip(safe_div(1.10-(atr_ratio or 1.10),.35)) +
                 7*_clip(safe_div(.30-(range20 or .30),.20)) +
                 5*_clip(safe_div(1.15-(volume_contraction or 1.15),.50)))
    setup=(5*_clip(safe_div(gap_fast,1.5)) +
           6*_clip(safe_div((near_high or 0)-.90,.10)) +
           4*_clip(safe_div((volume_ratio or 0)-.80,.70)) +
           4*_clip(1-abs((extension or 0)-.75)/2) +
           6*_clip(safe_div(closes[-1]/s50-.98,.06)))
    total=min(99.5, trend+relative_strength+contraction+setup)
    return round(total,2), {
        'ema9':e9,'ema21':e21,'ema200':e200,'sma50':s50,'volume_sma50':vol50,
        'stock_return20':stock20,'sector_return20':sector20,'relative_strength':rel20,
        'atr14':atr14,'extension_atr':extension,
        'pillars':{'trend':round(trend,2),'relative_strength':round(relative_strength,2),'contraction':round(contraction,2),'setup':round(setup,2)},
    }


def _rotation_history(bars_by_symbol: dict[str, list[dict]], cfg: dict, sessions: int = 200) -> list[dict]:
    """Sector diagnostics for each completed historical SPY session.

    Every point is calculated with its own prefix only.  It powers the
    dashboard lookback control and is intentionally separate from the score.
    """
    spy=bars_by_symbol[cfg['benchmark']]; spy_dates=[x['date'] for x in spy]
    lookup={symbol:{bar['date']:i for i,bar in enumerate(rows)} for symbol,rows in bars_by_symbol.items()}
    output=[]
    for date in spy_dates[-sessions:]:
        spy_index=lookup[cfg['benchmark']][date]
        if spy_index<20: continue
        spy_close=[x['close'] for x in spy[:spy_index+1]]
        for etf,tickers in cfg['sectors'].items():
            sector_bars=bars_by_symbol.get(etf,[]); index=lookup.get(etf,{}).get(date)
            if index is None or index<25: continue
            closes=[x['close'] for x in sector_bars[:index+1]]; relative=(pct_change(closes,20) or 0)-(pct_change(spy_close,20) or 0)
            acceleration=(pct_change(closes,5) or 0)-((closes[-6]/closes[-26]-1) if len(closes)>25 else 0)
            above=[]; bullish=[]
            for ticker in tickers:
                bars=bars_by_symbol.get(ticker,[]); t_index=lookup.get(ticker,{}).get(date)
                if t_index is None or t_index<50: continue
                tc=[x['close'] for x in bars[:t_index+1]]
                above.append(tc[-1]>=sma(tc,50)); bullish.append((ema(tc,9) or 0)>(ema(tc,21) or 0))
            if not above: continue
            breadth=100*sum(above)/len(above); breadth_ema=100*sum(bullish)/len(bullish)
            state=sector_state(relative, breadth, acceleration)
            output.append({'date':date,'etf':etf,'relative_strength20_pct':fmt(relative*100),'momentum20_pct':fmt((pct_change(closes,20) or 0)*100),'acceleration_pct':fmt(acceleration*100),'breadth_sma50_pct':fmt(breadth,1),'breadth_ema_pct':fmt(breadth_ema,1),'state':state})
    return output


def _asset_rotation_history(bars_by_symbol: dict[str, list[dict]], cfg: dict, sessions: int = 200) -> list[dict]:
    """Point-in-time rotation history for equities, separate from ETF breadth.

    A row exists only when that asset and SPY share the completed session.  The
    cross-sectional RS score is ranked anew for each date, never with a later
    universe observation.
    """
    spy = bars_by_symbol[cfg['benchmark']]
    spy_dates = [bar['date'] for bar in spy]
    spy_closes = [bar['close'] for bar in spy]
    closes_by_symbol = {symbol: [bar['close'] for bar in rows] for symbol, rows in bars_by_symbol.items()}
    lookup = {symbol: {bar['date']: index for index, bar in enumerate(rows)} for symbol, rows in bars_by_symbol.items()}
    sector_for_ticker = {ticker: sector for sector, tickers in cfg['sectors'].items() for ticker in tickers}
    output: list[dict] = []
    for date in spy_dates[-sessions:]:
        spy_index = lookup[cfg['benchmark']][date]
        if spy_index < 125:
            continue
        daily: list[dict] = []
        for ticker, sector in sector_for_ticker.items():
            bars = bars_by_symbol.get(ticker, [])
            index = lookup.get(ticker, {}).get(date)
            if index is None or index < 125:
                continue
            closes = closes_by_symbol[ticker]
            def relative_at(asset_index: int, benchmark_index: int) -> float | None:
                parts = []
                for span, weight in ((20, .50), (60, .30), (120, .20)):
                    asset_return = safe_div(closes[asset_index], closes[asset_index - span])
                    benchmark_return = safe_div(spy_closes[benchmark_index], spy_closes[benchmark_index - span])
                    if asset_return is None or benchmark_return is None:
                        return None
                    parts.append(((asset_return - 1) - (benchmark_return - 1)) * weight)
                return sum(parts)
            relative = relative_at(index, spy_index)
            prior = relative_at(index - 5, spy_index - 5)
            short_relative = safe_div(closes[index], closes[index - 20])
            short_spy = safe_div(spy_closes[spy_index], spy_closes[spy_index - 20])
            if relative is None or prior is None or short_relative is None or short_spy is None:
                continue
            acceleration = relative - prior
            state = sector_state(relative, 100 if closes[index] >= sma(closes[:index + 1], 50) else 0, acceleration)
            daily.append({
                'date': date, 'ticker': ticker, 'sector_etf': sector,
                'relative_strength20_pct': fmt(((short_relative - 1) - (short_spy - 1)) * 100),
                'rs_composite_raw': fmt(relative, 6), 'acceleration_pct': fmt(acceleration * 100),
                'state': state,
            })
        ranked = sorted(daily, key=lambda row: row['rs_composite_raw'])
        denominator = max(1, len(ranked) - 1)
        for rank, row in enumerate(ranked):
            row['rs_score'] = fmt(100 * rank / denominator, 1)
        output.extend(daily)
    return output


def build_snapshot(bars_by_symbol: dict[str, list[dict]], cfg: dict, put_call_by_date: dict[str, float] | None = None) -> dict:
    spy_bars = bars_by_symbol.get(cfg['benchmark'], []); qqq_bars = bars_by_symbol.get(cfg['growth_benchmark'], []); vix_bars = bars_by_symbol.get(cfg['volatility_symbol'], [])
    if not spy_bars: raise ValueError('SPY data unavailable')
    spy_close=[x['close'] for x in spy_bars]; assets=[]; sector_rows=[]; by_sector=defaultdict(list)
    for sector, tickers in cfg['sectors'].items():
        sector_bars=bars_by_symbol.get(sector, []); sector_close=[x['close'] for x in sector_bars]
        for ticker in tickers:
            bars=bars_by_symbol.get(ticker, []);
            if len(bars)<252 or len(sector_close)<21: continue
            closes=[x['close'] for x in bars]; volumes=[x['volume'] for x in bars]
            legacy_score,_=_score(closes,volumes,spy_close,sector_close)
            score, details=four_pillar_score(bars,spy_close,sector_close)
            if score is None: continue
            close=closes[-1]; atr=details['atr14']; spread=safe_div(details['ema9']-details['ema21'],details['ema21'])
            prior_score, _ = four_pillar_score(bars[:-1], spy_close[:-1], sector_close[:-1])
            score_cross_65 = bool(score >= OPERATIONAL_SCORE_THRESHOLD and prior_score is not None and prior_score < OPERATIONAL_SCORE_THRESHOLD)
            selective = selective_setup_score(bars, spy_bars)
            state='HIGH PRIORITY' if score>=cfg['score_thresholds']['high_priority'] else 'ARMED' if score>=cfg['score_thresholds']['armed'] else 'WATCH' if score>=cfg['score_thresholds']['watch'] else 'NEUTRAL'
            alerts=[]
            if close < details['sma50']: alerts.append('SMA50 RISK')
            if details['ema9'] < details['ema21']: alerts.append('WEAKENING')
            if atr and (close-details['ema21'])/atr > 2: alerts.append('EXTENDED / DO NOT CHASE')
            penalty=sum(PRESENTATION_PENALTIES.get(alert,0) for alert in alerts)
            stage,ema200_slope=_stage(bars); freshness=_setup_freshness(bars); rs_raw=_rs_composite(closes,spy_close); rs_prior=_rs_composite(closes[:-5],spy_close[:-5])
            participation=sessions_volume_metrics(closes,volumes)
            volume_ratio=safe_div(volumes[-1],details['volume_sma50'])
            daily_return=pct_change(closes,1)
            row={'ticker':ticker,'sector_etf':sector,'date':bars[-1]['date'],'close':fmt(close),'score':score,'legacy_score':legacy_score,'opportunity_score':round(max(0,score-penalty),2),'presentation_penalty':penalty,'state':state,'ema9':fmt(details['ema9']),'ema21':fmt(details['ema21']),'sma50':fmt(details['sma50']),'ema200':fmt(details['ema200']),'atr14':fmt(atr),'atr14_pct':fmt(safe_div(atr,close)*100),'daily_return_pct':fmt(daily_return*100 if daily_return is not None else None),'distance_sma50_pct':fmt(safe_div(close-details['sma50'],details['sma50'])*100),'distance_ema200_pct':fmt(safe_div(close-details['ema200'],details['ema200'])*100),'relative_strength20_pct':fmt(details['relative_strength']*100 if details['relative_strength'] is not None else None),'rs_composite_raw':fmt(rs_raw,6),'rs_change_5d_pct':fmt((rs_raw-rs_prior)*100 if rs_raw is not None and rs_prior is not None else None),'rs_score':None,'technical_stage':stage,'ema200_slope20_pct':fmt(ema200_slope),'setup_event':freshness['event'],'setup_age_sessions':freshness['age_sessions'],'fresh_setup':freshness['fresh'],'volume_ratio50':fmt(volume_ratio),'volume_deviation_pct':fmt((volume_ratio-1)*100 if volume_ratio is not None else None),'volume_direction':'ACCUMULATION' if daily_return and daily_return>0 else 'DISTRIBUTION' if daily_return and daily_return<0 else 'NEUTRAL',**participation,'wma8':fmt(wma(closes,8)),'wma21':fmt(wma(closes,21)),'sma30':fmt(sma(closes,30)),'d1_d2_spread_pct':fmt(spread*100 if spread is not None else None),'unusual_volume': bool(details['volume_sma50'] and volumes[-1] > details['volume_sma50']*1.5),'alerts':alerts,'detectors':independent_detectors(bars),'pillars':details['pillars'],'trend_60':_trend_window(bars),
                 'score_cross_65':score_cross_65,'selective_score_25_30_30_15':selective['score'],'selective_setup':selective['setup'],'selective_setup_reason':selective.get('setup_reason'),'selective_setup_reason_unavailable':selective.get('reason')}
            assets.append(row); by_sector[sector].append(row)
    for sector, members in by_sector.items():
        b50=100*sum(x['close']>=x['sma50'] for x in members)/len(members); bema=100*sum(x['ema9']>x['ema21'] for x in members)/len(members)
        sbars=bars_by_symbol.get(sector,[]); closes=[x['close'] for x in sbars]
        sr=pct_change(closes,20); spyr=pct_change(spy_close,20); acceleration=(pct_change(closes,5) or 0)-((closes[-6]/closes[-26]-1) if len(closes)>25 else 0)
        relative=(sr-spyr) if sr is not None and spyr is not None else None
        sector_rows.append({'etf':sector,'component_count':len(members),'benchmark_history_sessions':len(closes),'benchmark_history_start':sbars[0]['date'] if sbars else None,'benchmark_history_status':'LIMITED_HISTORY' if len(closes)<200 else 'OK','breadth_sma50_pct':fmt(b50,1),'breadth_ema_pct':fmt(bema,1),'relative_strength20_pct':fmt(relative*100 if relative is not None else None),'momentum20_pct':fmt(sr*100 if sr is not None else None),'acceleration_pct':fmt(acceleration*100),'watch_count':sum(x['state']=='WATCH' for x in members),'armed_count':sum(x['state']=='ARMED' for x in members),'high_priority_count':sum(x['state']=='HIGH PRIORITY' for x in members),'state':sector_state(relative,b50,acceleration)})
    _add_rs_percentiles(assets)
    gate = operational_gate(spy_bars, qqq_bars, vix_bars, assets, put_call_by_date)
    for asset in assets:
        clean = not any(alert in PRESENTATION_PENALTIES for alert in asset['alerts'])
        asset['eligible_for_new_entry'] = bool(gate['eligible_for_new_entries'] and asset['score_cross_65'] and clean)
        asset['selective_setup_candidate'] = bool(gate['eligible_for_new_entries'] and asset['selective_score_25_30_30_15'] is not None and asset['selective_score_25_30_30_15'] >= OPERATIONAL_SCORE_THRESHOLD and asset['selective_setup'] and clean)
    qclose=[x['close'] for x in qqq_bars]; vclose=[x['close'] for x in vix_bars]
    ratio=safe_div(qclose[-1],spy_close[-1]) if qclose else None; rel20=(pct_change(qclose,20) or 0)-(pct_change(spy_close,20) or 0) if qclose else None
    vix=vclose[-1] if vclose else None; vix_rank=percentile_rank(vclose[-252:],vix) if len(vclose)>=252 else None
    vix_state='STRESS' if vix and (vix>=35 or (vix_rank or 0)>=90) else 'ELEVATED' if vix and vix>=28 else 'CALM' if vix and vix<20 else 'NORMAL' if vix else 'DATA UNAVAILABLE'
    regime='RISK ON' if rel20 is not None and rel20>0 and vix_state in ('CALM','NORMAL') else 'STRESS' if vix_state=='STRESS' else 'CAUTION' if vix_state=='ELEVATED' or (rel20 is not None and rel20<0) else 'NEUTRAL'
    assets.sort(key=lambda x:(x['opportunity_score'],x['score']), reverse=True); sector_rows.sort(key=lambda x:x['relative_strength20_pct'] if x['relative_strength20_pct'] is not None else -999, reverse=True)
    return {'generated_at':datetime.now(timezone.utc).isoformat(),'as_of':spy_bars[-1]['date'],'source':'Yahoo Finance Chart API','market':{'regime':regime,'qqq_spy_ratio':fmt(ratio,4),'qqq_vs_spy_20d_pct':fmt(rel20*100 if rel20 is not None else None),'vix':fmt(vix),'vix_percentile252':fmt(vix_rank,1),'vix_state':vix_state,'operational_gate':gate},'assets':assets,'sectors':sector_rows,'rotation_history':_rotation_history(bars_by_symbol,cfg),'asset_rotation_history':_asset_rotation_history(bars_by_symbol,cfg),'status':'OK'}
