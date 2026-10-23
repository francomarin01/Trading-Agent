from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DATABASE = DATA / "market.sqlite3"


def connect() -> sqlite3.Connection:
    DATA.mkdir(exist_ok=True)
    con = sqlite3.connect(DATABASE)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript("""
    CREATE TABLE IF NOT EXISTS bars (
      symbol TEXT NOT NULL, date TEXT NOT NULL,
      open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,
      close REAL NOT NULL, volume REAL NOT NULL,
      PRIMARY KEY(symbol, date)
    );
    CREATE TABLE IF NOT EXISTS inclusions (
      ticker TEXT PRIMARY KEY, sector_etf TEXT NOT NULL, event TEXT NOT NULL,
      inclusion_date TEXT NOT NULL, entry_close REAL NOT NULL, entry_etf_close REAL
    );
    CREATE TABLE IF NOT EXISTS refresh_log (
      id INTEGER PRIMARY KEY AUTOINCREMENT, updated_at TEXT NOT NULL,
      status TEXT NOT NULL, message TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS signal_journal (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      model TEXT NOT NULL, ticker TEXT NOT NULL, sector_etf TEXT NOT NULL,
      signal_date TEXT NOT NULL, signal_score REAL NOT NULL, state TEXT NOT NULL,
      setup_reason TEXT, gate_snapshot TEXT NOT NULL,
      entry_date TEXT, entry_open REAL,
      status TEXT NOT NULL DEFAULT 'PENDING_ENTRY', updated_at TEXT NOT NULL,
      UNIQUE(model, ticker, signal_date)
    );
    CREATE TABLE IF NOT EXISTS watchlist (
      ticker TEXT PRIMARY KEY, sector_etf TEXT NOT NULL, added_date TEXT NOT NULL,
      added_at TEXT NOT NULL, position_status TEXT NOT NULL DEFAULT 'WATCHING'
    );
    CREATE TABLE IF NOT EXISTS top10_events (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ticker TEXT NOT NULL, sector_etf TEXT NOT NULL, signal_date TEXT NOT NULL,
      signal_score REAL NOT NULL, entry_rank INTEGER NOT NULL,
      entry_date TEXT, entry_open REAL, status TEXT NOT NULL DEFAULT 'PENDING_ENTRY',
      updated_at TEXT NOT NULL, UNIQUE(ticker, signal_date)
    );
    CREATE TABLE IF NOT EXISTS top10_membership (
      ticker TEXT PRIMARY KEY, is_top10 INTEGER NOT NULL, last_date TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS sec_ticker_map (
      source TEXT PRIMARY KEY, fetched_at TEXT NOT NULL, payload TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS fundamental_snapshots (
      ticker TEXT NOT NULL, source TEXT NOT NULL, fetched_at TEXT NOT NULL,
      status TEXT NOT NULL, filed_at TEXT, period_end TEXT, form TEXT, payload TEXT NOT NULL,
      PRIMARY KEY(ticker, source)
    );
    """)
    # Forward-compatible local migrations for an existing personal monitor.
    journal_columns = {row['name'] for row in con.execute('PRAGMA table_info(signal_journal)')}
    for column, definition in (
        ('signal_atr', 'REAL'), ('stop_atr_multiple', 'REAL'), ('target_r_multiple', 'REAL'),
    ):
        if column not in journal_columns:
            con.execute(f'ALTER TABLE signal_journal ADD COLUMN {column} {definition}')
    con.commit()
    return con


def upsert_bars(con: sqlite3.Connection, symbol: str, rows: list[tuple]) -> None:
    con.executemany(
        "INSERT OR REPLACE INTO bars(symbol,date,open,high,low,close,volume) VALUES(?,?,?,?,?,?,?)",
        [(symbol, *row) for row in rows],
    )
    con.commit()


def load_bars(con: sqlite3.Connection, symbol: str) -> list[dict]:
    return [dict(r) for r in con.execute(
        "SELECT date,open,high,low,close,volume FROM bars WHERE symbol=? ORDER BY date", (symbol,)
    )]
