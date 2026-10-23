from __future__ import annotations

import gzip
import json
import os
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


def company_fundamentals(ticker: str, cik: int, payload: dict[str, Any]) -> dict[str, Any]:
    revenue = _latest(_facts(payload, ('RevenueFromContractWithCustomerExcludingAssessedTax', 'Revenues', 'SalesRevenueNet')), True)
    net_income = _latest(_facts(payload, ('NetIncomeLoss', 'ProfitLoss')), True)
    operating_income = _latest(_facts(payload, ('OperatingIncomeLoss',)), True)
    operating_cash = _latest(_facts(payload, ('NetCashProvidedByUsedInOperatingActivities',)), True)
    capex = _latest(_facts(payload, ('PaymentsToAcquirePropertyPlantAndEquipment',)), True)
    cash = _latest(_facts(payload, ('CashAndCashEquivalentsAtCarryingValue', 'CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents')), False)
    debt = _latest(_facts(payload, ('LongTermDebtCurrent', 'LongTermDebtNoncurrent', 'LongTermDebt')), False)
    shares = _latest(_facts(payload, ('EntityCommonStockSharesOutstanding',), 'shares'), False)
    anchor = revenue or net_income or operating_income or operating_cash
    if anchor is None:
        return {'ticker': ticker, 'status': 'DATA UNAVAILABLE', 'reason': 'No comparable SEC XBRL facts', 'source': 'SEC EDGAR'}
    fcf = None
    if _value(operating_cash) is not None and _value(capex) is not None:
        fcf = _value(operating_cash) - abs(_value(capex) or 0)
    return {
        'ticker': ticker,
        'status': 'OK',
        'source': 'SEC EDGAR',
        'cik': f'{cik:010d}',
        'source_url': COMPANY_FACTS_URL.format(cik=cik),
        'filed_at': anchor.get('filed'),
        'period_end': anchor.get('end'),
        'form': anchor.get('form'),
        'revenue': _value(revenue),
        'net_income': _value(net_income),
        'operating_income': _value(operating_income),
        'operating_cash_flow': _value(operating_cash),
        'free_cash_flow': fcf,
        'cash': _value(cash),
        'debt': _value(debt),
        'shares_outstanding': _value(shares),
    }


def fetch_company_fundamentals(ticker: str, cik: int, user_agent: str) -> dict[str, Any]:
    payload = download_json(COMPANY_FACTS_URL.format(cik=cik), user_agent)
    return company_fundamentals(ticker, cik, payload)
