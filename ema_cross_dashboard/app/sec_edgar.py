from __future__ import annotations

import gzip
import json
import os
import re
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

TICKER_MAP_URL = 'https://www.sec.gov/files/company_tickers.json'
COMPANY_FACTS_URL = 'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json'
ALLOWED_FORMS = {'10-K', '10-K/A', '10-Q', '10-Q/A', '20-F', '20-F/A', '40-F', '40-F/A', '6-K', '6-K/A'}


class SecEdgarError(RuntimeError):
    pass


def configured_user_agent() -> str | None:
    value = os.environ.get('SEC_USER_AGENT', '').strip()
    return value or None


def _decode_response(raw: bytes, content_encoding: str | None = None) -> str:
    """Decode SEC JSON, including gzip responses seen behind some proxies."""
    encoding = (content_encoding or '').lower()
    if 'gzip' in encoding or raw[:2] == b'\x1f\x8b':
        raw = gzip.decompress(raw)
    return raw.decode('utf-8')


def download_json(url: str, user_agent: str, timeout: int = 30) -> dict[str, Any]:
    if not user_agent:
        raise SecEdgarError('SEC_USER_AGENT is required; set a contact identifier before querying SEC EDGAR')
    request = urllib.request.Request(
        url,
        headers={
            'User-Agent': user_agent,
            'Accept': 'application/json',
            'Accept-Encoding': 'gzip',
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(_decode_response(response.read(), response.headers.get('Content-Encoding')))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, gzip.BadGzipFile) as exc:
        raise SecEdgarError(f'SEC request failed: {exc}') from exc


def fetch_ticker_map(user_agent: str) -> dict[str, int]:
    payload = download_json(TICKER_MAP_URL, user_agent)
    rows = payload.values() if isinstance(payload, dict) else []
    mapping: dict[str, int] = {}
    for row in rows:
        ticker = str(row.get('ticker', '')).upper().strip()
        cik = row.get('cik_str')
        if ticker and isinstance(cik, int):
            mapping[ticker] = cik
    if not mapping:
        raise SecEdgarError('SEC ticker map did not contain valid ticker/CIK rows')
    return mapping


def _facts(payload: dict[str, Any], tags: tuple[str, ...], unit: str = 'USD') -> list[dict[str, Any]]:
    facts = payload.get('facts', {})
    namespaces = ('us-gaap', 'ifrs-full', 'dei')
    for namespace in namespaces:
        bucket = facts.get(namespace, {})
        for tag in tags:
            item = bucket.get(tag, {})
            rows = item.get('units', {}).get(unit, [])
            if rows:
                return [row for row in rows if row.get('form') in ALLOWED_FORMS and row.get('filed')]
    return []


def _latest(rows: list[dict[str, Any]], duration_only: bool = False) -> dict[str, Any] | None:
    rows = [row for row in rows if row.get('val') is not None]
    if duration_only:
        rows = [row for row in rows if row.get('start') and row.get('end')]
    if not rows:
        return None
    # Facts are public only when filed. Filed date is therefore the causal key.
    return max(rows, key=lambda row: (str(row.get('filed', '')), str(row.get('end', '')), str(row.get('fy', ''))))


def _value(row: dict[str, Any] | None) -> float | None:
    if row is None:
        return None
    try:
        return float(row['val'])
    except (KeyError, TypeError, ValueError):
        return None


def _duration_days(row: dict[str, Any]) -> int | None:
    try:
        return (date.fromisoformat(row['end']) - date.fromisoformat(row['start'])).days + 1
    except (KeyError, TypeError, ValueError):
        return None


def _quarterly_series(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return one filed, discrete-quarter fact per period end; ignore YTD facts."""
    periods: dict[str, dict[str, Any]] = {}
    for row in rows:
        days = _duration_days(row)
        end = row.get('end')
        if days is None or not 70 <= days <= 110 or not end or row.get('val') is None:
            continue
        prior = periods.get(end)
        if prior is None or str(row.get('filed', '')) > str(prior.get('filed', '')):
            periods[end] = row
    return sorted(periods.values(), key=lambda item: item['end'])


def _contiguous_quarters(rows: list[dict[str, Any]]) -> bool:
    return all(70 <= (date.fromisoformat(b['end']) - date.fromisoformat(a['end'])).days <= 110
               for a, b in zip(rows, rows[1:]))


def _ttm(rows: list[dict[str, Any]]) -> tuple[float | None, float | None]:
    """Return current and year-ago TTM sums only when reported periods align."""
    quarters = _quarterly_series(rows)
    if len(quarters) >= 8:
        current, prior = quarters[-4:], quarters[-8:-4]
        if _contiguous_quarters(current) and _contiguous_quarters(prior):
            separation = (date.fromisoformat(current[-1]['end']) - date.fromisoformat(prior[-1]['end'])).days
            if 330 <= separation <= 400:
                return sum(float(row['val']) for row in current), sum(float(row['val']) for row in prior)
    if len(quarters) >= 4:
        current = quarters[-4:]
        if _contiguous_quarters(current):
            return sum(float(row['val']) for row in current), None

    annual: dict[str, dict[str, Any]] = {}
    for row in rows:
        days = _duration_days(row)
        if days is not None and 330 <= days <= 400 and row.get('val') is not None:
            prior = annual.get(row['end'])
            if prior is None or str(row.get('filed', '')) > str(prior.get('filed', '')):
                annual[row['end']] = row
    ordered = sorted(annual.values(), key=lambda item: item['end'])
    if ordered:
        current = ordered[-1]
        previous = ordered[-2] if len(ordered) > 1 else None
        if previous and 330 <= (date.fromisoformat(current['end']) - date.fromisoformat(previous['end'])).days <= 400:
            return float(current['val']), float(previous['val'])
        return float(current['val']), None
    return None, None


def _instant(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    rows = [row for row in rows if row.get('val') is not None and row.get('end')]
    return max(rows, key=lambda row: (str(row.get('filed', '')), str(row.get('end', '')))) if rows else None


def _per_share_facts(payload: dict[str, Any], tags: tuple[str, ...]) -> list[dict[str, Any]]:
    for namespace in ('us-gaap', 'ifrs-full'):
        bucket = payload.get('facts', {}).get(namespace, {})
        for tag in tags:
            item = bucket.get(tag, {})
            for unit, rows in item.get('units', {}).items():
                normalized = re.sub(r'\s+', '', unit.lower())
                if 'usd' in normalized and ('share' in normalized or 'shares' in normalized):
                    valid = [row for row in rows if row.get('form') in ALLOWED_FORMS and row.get('filed')]
                    if valid:
                        return valid
    return []


def company_fundamentals(ticker: str, cik: int, payload: dict[str, Any]) -> dict[str, Any]:
    revenue_rows = _facts(payload, ('RevenueFromContractWithCustomerExcludingAssessedTax', 'Revenues', 'SalesRevenueNet'))
    income_rows = _facts(payload, ('NetIncomeLoss', 'ProfitLoss'))
    operating_rows = _facts(payload, ('OperatingIncomeLoss',))
    operating_cash_rows = _facts(payload, ('NetCashProvidedByUsedInOperatingActivities',))
    capex_rows = _facts(payload, ('PaymentsToAcquirePropertyPlantAndEquipment',))
    depreciation_rows = _facts(payload, ('DepreciationDepletionAndAmortization', 'DepreciationDepletionAndAmortizationPropertyPlantAndEquipment'))
    revenue, revenue_prior = _ttm(revenue_rows)
    net_income, net_income_prior = _ttm(income_rows)
    operating_income, _ = _ttm(operating_rows)
    operating_cash, _ = _ttm(operating_cash_rows)
    capex, _ = _ttm(capex_rows)
    depreciation, _ = _ttm(depreciation_rows)
    eps_rows = _per_share_facts(payload, ('EarningsPerShareDiluted', 'EarningsPerShareBasic'))
    eps_ttm, eps_prior = _ttm(eps_rows)
    cash = _instant(_facts(payload, ('CashAndCashEquivalentsAtCarryingValue', 'CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents'), 'USD'))
    long_debt_noncurrent = _instant(_facts(payload, ('LongTermDebtNoncurrent',), 'USD'))
    long_debt_total = _instant(_facts(payload, ('LongTermDebt',), 'USD'))
    current_debt = _instant(_facts(payload, ('LongTermDebtCurrent', 'ShortTermBorrowings'), 'USD'))
    debt = None
    if long_debt_noncurrent is not None:
        debt = _value(long_debt_noncurrent) + (_value(current_debt) or 0.0)
    elif long_debt_total is not None:
        debt = _value(long_debt_total)
    elif current_debt is not None:
        debt = _value(current_debt)
    equity = _instant(_facts(payload, ('StockholdersEquity', 'StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest', 'StockholdersEquityIncludingPortionAttributableToRedeemableNoncontrollingInterest'), 'USD'))
    shares = _instant(_facts(payload, ('EntityCommonStockSharesOutstanding',), 'shares'))
    anchor = next((row for row in (
        _latest(revenue_rows, True), _latest(income_rows, True), _latest(operating_rows, True), _latest(operating_cash_rows, True)
    ) if row), None)
    if anchor is None:
        return {'ticker': ticker, 'status': 'DATA UNAVAILABLE', 'reason': 'No comparable SEC XBRL facts', 'source': 'SEC EDGAR'}
    fcf = None
    if operating_cash is not None and capex is not None:
        fcf = operating_cash - abs(capex)
    eps_growth = None
    if eps_ttm is not None and eps_prior is not None and eps_prior > 0:
        eps_growth = 100 * (eps_ttm / eps_prior - 1)
    revenue_growth = None
    if revenue is not None and revenue_prior is not None and revenue_prior > 0:
        revenue_growth = 100 * (revenue / revenue_prior - 1)
    return {
        'ticker': ticker,
        'status': 'OK',
        'source': 'SEC EDGAR',
        'cik': f'{cik:010d}',
        'source_url': COMPANY_FACTS_URL.format(cik=cik),
        'filed_at': anchor.get('filed'),
        'period_end': anchor.get('end'),
        'form': anchor.get('form'),
        'revenue_ttm': revenue,
        'revenue_growth_yoy_pct': revenue_growth,
        'net_income_ttm': net_income,
        'operating_income_ttm': operating_income,
        'ebitda_ttm': operating_income + depreciation if operating_income is not None and depreciation is not None else None,
        'eps_diluted_ttm': eps_ttm,
        'eps_growth_yoy_pct': eps_growth,
        'operating_cash_flow_ttm': operating_cash,
        'free_cash_flow': fcf,
        'cash': _value(cash),
        'debt': debt,
        'shareholders_equity': _value(equity),
        'shares_outstanding': _value(shares),
    }


def valuation_metrics(record: dict[str, Any], price: float | None) -> dict[str, Any]:
    """Derive current valuation multiples without changing the technical score."""
    try:
        price = float(price) if price is not None else None
    except (TypeError, ValueError):
        price = None
    shares, income = record.get('shares_outstanding'), record.get('net_income_ttm')
    market_cap = price * shares if price and price > 0 and shares and shares > 0 else None
    ev = market_cap + record['debt'] - record['cash'] if market_cap is not None and record.get('debt') is not None and record.get('cash') is not None else None
    eps = record.get('eps_diluted_ttm')
    pe = price / eps if price and eps and eps > 0 else (market_cap / income if market_cap and income and income > 0 else None)
    eps_growth = record.get('eps_growth_yoy_pct')
    peg = pe / eps_growth if pe is not None and eps_growth is not None and eps_growth > 0 else None
    revenue = record.get('revenue_ttm')
    ebitda = record.get('ebitda_ttm')
    equity = record.get('shareholders_equity')
    fcf = record.get('free_cash_flow')
    return {
        'market_cap_indicative': market_cap,
        'enterprise_value_indicative': ev,
        'price_to_earnings': pe,
        'peg_historical': peg,
        'ev_to_sales': ev / revenue if ev is not None and revenue and revenue > 0 else None,
        'ev_to_ebitda': ev / ebitda if ev is not None and ebitda and ebitda > 0 else None,
        'price_to_book': market_cap / equity if market_cap is not None and equity and equity > 0 else None,
        'free_cash_flow_yield_pct': 100 * fcf / market_cap if fcf is not None and market_cap and market_cap > 0 else None,
        'revenue_growth_yoy_pct': record.get('revenue_growth_yoy_pct'),
        'eps_growth_yoy_pct': eps_growth,
        'price_basis': 'Yahoo Finance latest daily close',
        'market_cap_basis': 'Latest SEC shares outstanding × latest daily close; indicative and may differ for ADRs/share classes',
    }


def fetch_company_fundamentals(ticker: str, cik: int, user_agent: str) -> dict[str, Any]:
    payload = download_json(COMPANY_FACTS_URL.format(cik=cik), user_agent)
    return company_fundamentals(ticker, cik, payload)
