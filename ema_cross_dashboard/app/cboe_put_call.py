"""Reproducible research-only import of official Cboe TOTAL put/call ratios."""
from __future__ import annotations
import argparse
import csv
import gzip
import hashlib
import json
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "research" / "cboe_total_put_call.csv"
META = OUT.with_suffix(".provenance.json")
URL = "https://www.cboe.com/markets/us/options/market-statistics/daily?dt="
FIELDS = ["Date", "PutCall", "Source", "DownloadedAtUTC", "PageSHA256"]
_lock = threading.Lock()
_last_request = 0.0

def parse_page(html: str, requested_date: str) -> float:
    dates = set(re.findall(r"selectedDate.{0,20}?(\d{4}-\d{2}-\d{2})", html))
    if dates != {requested_date}:
        raise ValueError(f"Cboe date mismatch: requested {requested_date}, received {sorted(dates)}")
    found = re.search(r"<td[^>]*>TOTAL PUT/CALL RATIO</td>\s*<td[^>]*>([0-9.]+)</td>", html)
    if not found:
        raise ValueError("TOTAL PUT/CALL RATIO missing in dated Cboe response")
    value = float(found.group(1))
    if not 0 <= value < 100:
        raise ValueError("Cboe ratio outside valid numeric bounds")
    return value

def _fetch(date: str) -> dict:
    global _last_request
    error = None
    for attempt in range(3):
        with _lock:
            time.sleep(max(0.0, 0.5 - (time.monotonic() - _last_request)))
            _last_request = time.monotonic()
        try:
            req = urllib.request.Request(URL + date, headers={"User-Agent":"Mozilla/5.0 EMA-Cross-Research", "Accept-Encoding":"gzip"})
            with urllib.request.urlopen(req, timeout=30) as response:
                raw = response.read()
                if raw[:2] == b"\x1f\x8b":
                    raw = gzip.decompress(raw)
            value = parse_page(raw.decode("utf-8"), date)
            return {"Date":date, "PutCall":value,"Source":URL+date,
                    "DownloadedAtUTC":datetime.now(timezone.utc).isoformat(),
                    "PageSHA256":hashlib.sha256(raw).hexdigest()}
        except urllib.error.HTTPError as exc:
            error = str(exc)
            if exc.code < 500:
                break
        except (OSError, ValueError, UnicodeError) as exc:
            error = str(exc)
        time.sleep(1 + attempt)
    return {"Date":date,"error":error}

def load_ratios(path: Path = OUT) -> dict[str, float]:
    if not path.exists():
        return {}
    out = {}
    with path.open(encoding="utf-8-sig",newline="") as f:
        for row in csv.DictReader(f):
            date = row["Date"]
            if datetime.strptime(date, "%Y-%m-%d").date().isoformat()!=date:
                raise ValueError(f"Unnormalized Cboe date {date}")
            value = float(row["PutCall"])
            if date in out or not 0 <= value < 100 or row["Source"] != URL+date or not re.fullmatch(r"[0-9a-f]{64}",row["PageSHA256"]):
                raise ValueError(f"Invalid or duplicated Cboe provenance for {date}")
            if datetime.fromisoformat(row['DownloadedAtUTC']).tzinfo is None:
                raise ValueError(f"Missing download timezone for Cboe {date}")
            out[date] = value
    return out

def _save(rows: dict, dates: list[str], errors: list[dict]) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    temp = OUT.with_suffix(".tmp")
    with temp.open("w",encoding="utf-8",newline="") as f:
        writer = csv.DictWriter(f,fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows[d] for d in sorted(rows))
    temp.replace(OUT)
    META.write_text(json.dumps({"status":"OK" if all(d in rows for d in dates) else "PARTIAL",
        "source":URL+"YYYY-MM-DD", "ratio_type":"CBOE_TOTAL_REPORTED_DAILY",
        "requested_sessions":len(dates),"available_sessions":sum(d in rows for d in dates),
        "missing_dates":[d for d in dates if d not in rows],"errors":errors,
        "first":min(rows) if rows else None,"last":max(rows) if rows else None,
        "publication_time":"NOT_AVAILABLE_USE_STRICT_PREVIOUS_SPY_SESSION",
        "downloaded_at_utc":datetime.now(timezone.utc).isoformat()},indent=2),encoding="utf-8")

def refresh(as_of: str) -> dict:
    con = sqlite3.connect((ROOT/"data"/"market.sqlite3").resolve().as_uri()+"?mode=ro",uri=True)
    dates = [r[0] for r in con.execute("SELECT date FROM bars WHERE symbol='SPY' AND date<=? ORDER BY date",(as_of,))]
    con.close()
    rows = {}
    if OUT.exists():
        load_ratios()  # validate before reusing cached observations
        with OUT.open(encoding="utf-8-sig",newline="") as f:
            rows = {r["Date"]:r for r in csv.DictReader(f)}
    missing = [d for d in dates if d not in rows]
    errors = []
    consecutive_errors = 0
    pool = ThreadPoolExecutor(max_workers=4)
    try:
        for i, row in enumerate(pool.map(_fetch, missing), 1):
            if "error" in row:
                errors.append(row); consecutive_errors += 1
            else:
                rows[row["Date"]] = row; consecutive_errors = 0
            if i % 25 == 0 or i == len(missing):
                _save(rows, dates, errors)
                print(f"Cboe {sum(d in rows for d in dates)}/{len(dates)} · failures {len(errors)}",flush=True)
            if consecutive_errors >= 5:
                print("Stopped after five consecutive failures; no values imputed.",flush=True)
                break
    finally:
        pool.shutdown(wait=True,cancel_futures=True)
        _save(rows,dates,errors)
    return json.loads(META.read_text())

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of",default="2026-10-12")
    args=parser.parse_args()
    result=refresh(args.as_of)
    print(f"Cboe download: {result['status']} · {result['available_sessions']} valid dated observations")
