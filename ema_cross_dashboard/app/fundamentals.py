from __future__ import annotations

import csv
import json
import math
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
INCOMING = ROOT / 'data' / 'incoming'
FUNDAMENTALS_FILE = INCOMING / 'finviz_fundamentals.json'
SOURCE = 'Finviz CSV export'
SCHEMA_VERSION = 3
MAX_AGE_DAYS = 7

METRIC_ALIASES: dict[str, tuple[str, ...]] = {
    'trailing_pe': ('P/E', 'PE'),
    'forward_pe': ('Forward P/E', 'Fwd P/E'),
    'peg_forward': ('PEG',),
    'ev_sales': ('EV/Sales', 'EV/Revenue', 'Enterprise Value/Sales', 'Enterprise Value/Revenue'),
    'ev_ebitda': ('EV/EBITDA', 'Enterprise Value/EBITDA'),
    'price_to_sales': ('P/S', 'Price/Sales'),
    'price_to_book': ('P/B', 'Price/Book'),
    'roe_pct': ('ROE', 'Return on Equity'),
    'roa_pct': ('ROA', 'Return on Assets'),
    'roic_pct': ('ROIC', 'Return on Invested Capital'),
    'gross_margin_pct': ('Gross Margin',),
    'operating_margin_pct': ('Oper. Margin', 'Operating Margin'),
    'profit_margin_pct': ('Profit Margin', 'Net Margin'),
    'eps_growth_next_5y_pct': ('EPS growth next 5 years', 'EPS next 5Y', 'EPS growth next 5Y'),
    'eps_growth_next_year_pct': ('EPS growth next fiscal year', 'EPS next Y', 'EPS growth next Y'),
    'eps_growth_this_year_pct': ('EPS growth this fiscal year', 'EPS this Y', 'EPS growth this Y'),
    'eps_growth_past_5y_pct': ('EPS growth past 5 years', 'EPS past 5Y', 'EPS growth past 5Y'),
    'revenue_growth_next_5y_pct': ('Sales growth next 5 years', 'Sales next 5Y'),
    'revenue_growth_past_5y_pct': ('Sales growth past 5 years', 'Sales past 5Y'),
    'revenue_growth_qoq_pct': ('Sales Q/Q', 'Sales growth Q/Q'),
    'earnings_growth_qoq_pct': ('EPS Q/Q', 'EPS growth Q/Q'),
    'debt_to_equity': ('Debt/Eq', 'Debt/Equity'),
    'lt_debt_to_equity': ('LT Debt/Eq', 'Long Term Debt/Equity'),
    'fcf_yield_pct': ('FCF Yield', 'Free Cash Flow Yield'),
}

PERCENT_METRICS = {name for name in METRIC_ALIASES if name.endswith('_pct')}


class FinvizImportError(ValueError):
    pass


def _header_key(value: Any) -> str:
    return re.sub(r'[^a-z0-9]+', '', str(value or '').lower())


def _number(value: Any, percent: bool = False) -> float | None:
    raw = str(value or '').strip()
    if not raw or raw.upper() in {'-', '—', 'N/A', 'NA', 'DATA UNAVAILABLE', 'NONE'}:
        return None
    raw = raw.replace(',', '').replace('$', '').replace('−', '-')
    had_percent = '%' in raw
    raw = raw.replace('%', '').strip()
    multiplier = 1.0
    if raw and raw[-1:].upper() in {'K', 'M', 'B', 'T'}:
        multiplier = {'K': 1e3, 'M': 1e6, 'B': 1e9, 'T': 1e12}[raw[-1].upper()]
        raw = raw[:-1].strip()
    try:
        result = float(raw) * multiplier
    except ValueError:
        return None
    if not math.isfinite(result):
        return None
    # Finviz exports percentages with a percent sign. Permit plain numeric
    # percentages as-is because the screener exports already use percentage units.
    return result if percent or had_percent else result


def import_finviz_csv(input_path: str | Path, as_of: str, output_path: str | Path | None = None) -> dict[str, Any]:
    """Normalize a user-exported Finviz screener CSV without network access."""
    try:
        export_date = date.fromisoformat(as_of)
    except (TypeError, ValueError) as exc:
        raise FinvizImportError('La fecha de exportación debe usar YYYY-MM-DD.') from exc
    if export_date > datetime.now(timezone.utc).date():
        raise FinvizImportError('La fecha de exportación no puede estar en el futuro.')
    path = Path(input_path)
    if not path.is_file():
        raise FinvizImportError(f'No se encontró el CSV: {path}')
    with path.open('r', encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames:
            raise FinvizImportError('El archivo CSV no tiene encabezados.')
        headers = {_header_key(name): name for name in reader.fieldnames}
        required = {'ticker': ('Ticker',), 'sector': ('Sector',), 'industry': ('Industry',)}
        columns = {}
        for field, aliases in required.items():
            column = next((headers.get(_header_key(alias)) for alias in aliases if headers.get(_header_key(alias))), None)
            if not column:
                raise FinvizImportError(f'Falta la columna requerida: {field.title()}.')
            columns[field] = column
        metric_columns = {}
        for metric, aliases in METRIC_ALIASES.items():
            column = next((headers.get(_header_key(alias)) for alias in aliases if headers.get(_header_key(alias))), None)
            if column:
                metric_columns[metric] = column
        if not metric_columns:
            raise FinvizImportError('No se encontraron columnas fundamentales reconocidas en el CSV.')
        rows: dict[str, dict[str, Any]] = {}
        for line_number, row in enumerate(reader, start=2):
            ticker = str(row.get(columns['ticker']) or '').strip().upper()
            if not ticker:
                raise FinvizImportError(f'Fila {line_number}: falta el ticker.')
            if not re.fullmatch(r'[A-Z0-9.^_-]{1,20}', ticker):
                raise FinvizImportError(f'Fila {line_number}: ticker inválido {ticker!r}.')
            if ticker in rows:
                raise FinvizImportError(f'Fila {line_number}: ticker duplicado {ticker}.')
            sector = str(row.get(columns['sector']) or '').strip()
            industry = str(row.get(columns['industry']) or '').strip()
            metrics = {
                metric: _number(row.get(column), percent=metric in PERCENT_METRICS)
                for metric, column in metric_columns.items()
            }
            rows[ticker] = {
                'schema_version': SCHEMA_VERSION,
                'ticker': ticker,
                'status': 'OK' if any(value is not None for value in metrics.values()) else 'DATA UNAVAILABLE',
                'source': SOURCE,
                'source_url': f'https://finviz.com/quote.ashx?t={ticker}',
                'updated_at': export_date.isoformat(),
                'sector': sector or None,
                'industry': industry or None,
                'metrics': metrics,
                'peg_basis': 'PEG de Finviz: Forward P/E respecto a crecimiento EPS proyectado de 3 a 5 años',
            }
    if not rows:
        raise FinvizImportError('El CSV no contiene filas de activos.')
    payload = {
        'schema_version': SCHEMA_VERSION,
        'source': SOURCE,
        'as_of': export_date.isoformat(),
        'imported_at': datetime.now(timezone.utc).isoformat(),
        'row_count': len(rows),
        'records': rows,
        'notes': 'Exportación manual del screener. No se descarga información automáticamente.',
    }
    destination = Path(output_path) if output_path else FUNDAMENTALS_FILE
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + '.tmp')
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(destination)
    return payload


def peer_context(assets: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Industry-first averages excluding the subject; only descriptive context."""
    output: dict[str, dict[str, Any]] = {}
    for asset in assets:
        record = asset.get('fundamentals') or {}
        sector, industry = record.get('sector'), record.get('industry')
        if not sector:
            output[asset['ticker']] = {'peer_group': None, 'peer_count': 0, 'averages': {}, 'counts': {}}
            continue
        peers = [other for other in assets if other['ticker'] != asset['ticker']
                 and (other.get('fundamentals') or {}).get('sector') == sector
                 and industry and (other.get('fundamentals') or {}).get('industry') == industry]
        group = industry
        if not peers:
            peers = [other for other in assets if other['ticker'] != asset['ticker']
                     and (other.get('fundamentals') or {}).get('sector') == sector]
            group = sector if peers else None
        names = set((record.get('metrics') or {}).keys())
        for peer in peers:
            names.update(((peer.get('fundamentals') or {}).get('metrics') or {}).keys())
        averages, counts = {}, {}
        for name in names:
            values = []
            for peer in peers:
                try:
                    value = float(((peer.get('fundamentals') or {}).get('metrics') or {}).get(name))
                except (TypeError, ValueError):
                    continue
                if math.isfinite(value):
                    values.append(value)
            counts[name] = len(values)
            averages[name] = sum(values) / len(values) if values else None
        output[asset['ticker']] = {'peer_group': group, 'peer_count': len(peers), 'averages': averages, 'counts': counts}
    return output


def refresh_fundamentals(con, assets: list[dict], cfg: dict[str, Any]) -> dict[str, Any]:
    """Load the latest imported Finviz CSV snapshot; never fetch or infer values."""
    del con
    settings = cfg.get('fundamentals', {})
    maximum_age = max(1, int(settings.get('max_age_days', MAX_AGE_DAYS)))
    result: dict[str, Any] = {
        'source': SOURCE, 'status': 'DATA UNAVAILABLE', 'as_of': None, 'updated_at': None,
        'imported_at': None, 'selected_count': len(assets), 'refreshed_count': 0,
        'covered_count': 0, 'stale_count': 0, 'errors': [], 'max_age_days': maximum_age,
        'peer_method': 'Promedio simple de empresas de la misma industria, excluyendo la empresa; usa el promedio del sector sólo si no hay pares de industria.',
        'note': 'Los fundamentals son informativos y no modifican score, señales ni backtests.',
    }
    input_file = Path(settings.get('snapshot_path') or FUNDAMENTALS_FILE)
    if not input_file.is_absolute():
        input_file = ROOT / input_file
    try:
        payload = json.loads(input_file.read_text(encoding='utf-8'))
        as_of = date.fromisoformat(payload['as_of'])
        records = payload['records']
        if payload.get('schema_version') != SCHEMA_VERSION or payload.get('source') != SOURCE or not isinstance(records, dict):
            raise FinvizImportError('El snapshot Finviz tiene un esquema o fuente no reconocidos.')
        age = (datetime.now(timezone.utc).date() - as_of).days
        if age < 0:
            raise FinvizImportError('El snapshot Finviz tiene una fecha futura.')
        stale = age > maximum_age
        result.update({'status': 'STALE' if stale else 'OK', 'as_of': as_of.isoformat(),
                       'updated_at': as_of.isoformat(), 'imported_at': payload.get('imported_at'),
                       'stale_count': len(records) if stale else 0,
                       'note': f"Exportación Finviz de {as_of.isoformat()} · {'desactualizada' if stale else 'actual'}; actualizala con un nuevo CSV."})
        for asset in assets:
            ticker = asset.get('ticker')
            record = records.get(ticker)
            if not record or record.get('source') != SOURCE or record.get('schema_version') != SCHEMA_VERSION:
                asset['fundamentals'] = {'ticker': ticker, 'status': 'DATA UNAVAILABLE', 'source': SOURCE, 'reason': 'Ticker ausente en la última exportación Finviz'}
                asset['valuation'] = {}
                continue
            record = dict(record)
            if stale:
                record['freshness'] = 'STALE'
            asset['fundamentals'] = record
            asset['valuation'] = record.get('metrics') or {}
            result['covered_count'] += 1
            result['refreshed_count'] += 1
    except FileNotFoundError:
        result['errors'].append(f'No se encontró la exportación Finviz: {input_file}')
        result['note'] = 'DATA UNAVAILABLE · Importá un CSV exportado desde FINVIZ.'
        for asset in assets:
            asset['fundamentals'] = {'ticker': asset.get('ticker'), 'status': 'DATA UNAVAILABLE', 'source': SOURCE, 'reason': 'Aún no se importó el CSV Finviz'}
            asset['valuation'] = {}
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError, FinvizImportError) as exc:
        result['errors'].append(str(exc))
        result['note'] = f'DATA UNAVAILABLE · {exc}'
        for asset in assets:
            asset['fundamentals'] = {'ticker': asset.get('ticker'), 'status': 'DATA UNAVAILABLE', 'source': SOURCE, 'reason': str(exc)}
            asset['valuation'] = {}
    peers = peer_context(assets)
    for asset in assets:
        if asset.get('ticker'):
            asset['fundamental_peers'] = peers.get(asset['ticker'], {})
    return result
