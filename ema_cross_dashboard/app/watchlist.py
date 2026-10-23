"""User-managed watchlist and deliberate paper-position actions."""
from __future__ import annotations

from datetime import date, datetime, timezone

from .journal import register_manual_position


def _asset(snapshot: dict, ticker: str) -> dict:
    ticker = ticker.strip().upper()
    asset = next((item for item in snapshot.get('assets', []) if item['ticker'] == ticker), None)
    if not asset:
        raise ValueError('Ticker no disponible en el snapshot actual')
    return asset


def add_to_watchlist(con, snapshot: dict, ticker: str) -> bool:
    asset = _asset(snapshot, ticker)
    cursor = con.execute(
        "INSERT OR IGNORE INTO watchlist(ticker,sector_etf,added_date,added_at,position_status) VALUES(?,?,?,?,?)",
        (asset['ticker'], asset['sector_etf'], snapshot.get('as_of') or asset['date'], datetime.now(timezone.utc).isoformat(), 'WATCHING'),
    )
    con.commit()
    return bool(cursor.rowcount)


def remove_from_watchlist(con, ticker: str) -> bool:
    cursor = con.execute('DELETE FROM watchlist WHERE ticker=?', (ticker.strip().upper(),))
    con.commit()
    return bool(cursor.rowcount)


def _validate_execution(entry_price: object, entry_date: object) -> tuple[float, str]:
    try:
        price = float(entry_price)
    except (TypeError, ValueError) as exc:
        raise ValueError('El precio de ejecución debe ser numérico') from exc
    if price <= 0:
        raise ValueError('El precio de ejecución debe ser mayor que cero')
    try:
        executed = date.fromisoformat(str(entry_date))
    except ValueError as exc:
        raise ValueError('La fecha de ejecución debe usar YYYY-MM-DD') from exc
    if executed > date.today():
        raise ValueError('La fecha de ejecución no puede estar en el futuro')
    return round(price, 6), executed.isoformat()


def open_watchlist_position(con, snapshot: dict, ticker: str, entry_price: object = None, entry_date: object = None) -> bool:
    """Record the actual discretionary fill; legacy pending rows can be completed."""
    asset = _asset(snapshot, ticker)
    existing = con.execute('SELECT 1 FROM watchlist WHERE ticker=?', (asset['ticker'],)).fetchone()
    if not existing:
        raise ValueError('Agregá el ticker a la watchlist antes de abrir seguimiento')
    price, executed = _validate_execution(entry_price, entry_date)
    pending = con.execute("SELECT id FROM signal_journal WHERE model='MANUAL_WATCHLIST' AND ticker=? AND status='PENDING_ENTRY'", (asset['ticker'],)).fetchone()
    if pending:
        con.execute("UPDATE signal_journal SET entry_date=?, entry_open=?, status='ACTIVE', updated_at=? WHERE id=?", (executed, price, datetime.now(timezone.utc).isoformat(), pending['id']))
        created = True
    else:
        created = register_manual_position(con, asset, snapshot.get('market', {}).get('operational_gate', {}), snapshot.get('risk_plan', {}), price, executed)
    if created:
        con.execute("UPDATE watchlist SET position_status='ACTIVE' WHERE ticker=?", (asset['ticker'],))
        con.commit()
    return created


def render_watchlist(con, snapshot: dict, journal: list[dict]) -> list[dict]:
    assets = {asset['ticker']: asset for asset in snapshot.get('assets', [])}
    manual = {row['ticker']: row for row in journal if row['model'] == 'MANUAL_WATCHLIST'}
    rows = []
    for raw in con.execute('SELECT * FROM watchlist ORDER BY added_date DESC, ticker'):
        item = dict(raw); asset = assets.get(item['ticker']); position = manual.get(item['ticker'])
        status = position['status'] if position else item['position_status']
        con.execute('UPDATE watchlist SET position_status=? WHERE ticker=?', (status, item['ticker']))
        rows.append({
            **item, 'position_status': status,
            'score': asset.get('score') if asset else None, 'state': asset.get('state') if asset else 'DATA UNAVAILABLE',
            'close': asset.get('close') if asset else None, 'alerts': asset.get('alerts', []) if asset else [],
            'entry_date': position.get('entry_date') if position else None,
            'current_return_pct': position.get('current_return_pct') if position else None,
            'stop_price': position.get('stop_price') if position else None,
            'target_price': position.get('target_price') if position else None,
            'risk_status': position.get('risk_status') if position else 'WATCHING',
        })
    con.commit()
    return rows
