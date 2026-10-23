"""Versioned S&P 500 universe importer with sector ETF mapping."""
from __future__ import annotations

import copy
import json
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

from .market import config

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / 'config' / 'universe.json'
CORE_PATH = ROOT / 'config' / 'universe_core.json'

GICS_TO_ETF = {
    'Information Technology': 'XLK', 'Health Care': 'XLV', 'Financials': 'XLF',
    'Consumer Discretionary': 'XLY', 'Consumer Staples': 'XLP', 'Energy': 'XLE',
    'Industrials': 'XLI', 'Utilities': 'XLU', 'Communication Services': 'XLC',
    'Materials': 'XLB', 'Real Estate': 'XLRE',
}


class ConstituentsTable(HTMLParser):
    def __init__(self):
        super().__init__(); self.in_target=False; self.in_row=False; self.in_cell=False; self.rows=[]; self.row=[]; self.text=[]
    def handle_starttag(self, tag, attrs):
        attr=dict(attrs)
        if tag=='table' and attr.get('id')=='constituents': self.in_target=True
        elif self.in_target and tag=='tr': self.in_row=True; self.row=[]
        elif self.in_row and tag in ('td','th'): self.in_cell=True; self.text=[]
    def handle_data(self, data):
        if self.in_cell: self.text.append(data)
    def handle_endtag(self, tag):
        if tag in ('td','th') and self.in_cell: self.row.append(''.join(self.text).strip()); self.in_cell=False
        elif tag=='tr' and self.in_row:
            if len(self.row)>=3 and self.row[0] != 'Symbol': self.rows.append(self.row)
            self.in_row=False
        elif tag=='table' and self.in_target: self.in_target=False


def fetch_constituents() -> list[tuple[str,str]]:
    url='https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
    req=urllib.request.Request(url,headers={'User-Agent':'EMA-Cross-Dashboard/1.0 (personal research)'})
    with urllib.request.urlopen(req,timeout=30) as response: html=response.read().decode('utf-8')
    parser=ConstituentsTable(); parser.feed(html)
    mapped=[]
    for row in parser.rows:
        symbol=row[0].replace('.', '-').strip(); sector=GICS_TO_ETF.get(row[2].strip())
        if symbol and sector: mapped.append((symbol,sector))
    if len(mapped)<450: raise ValueError(f'Expected at least 450 S&P 500 constituents; found {len(mapped)}')
    return mapped


def apply_sp500(constituents: list[tuple[str,str]]) -> dict:
    current=config()
    if not CORE_PATH.exists(): CORE_PATH.write_text(json.dumps(current,ensure_ascii=False,indent=2),encoding='utf-8')
    sectors={etf:[] for etf in GICS_TO_ETF.values()}
    for symbol,etf in constituents: sectors[etf].append(symbol)

    # Keep curated lenses when the S&P 500 base is refreshed.  Their members
    # deliberately use the thematic/country ETF as benchmark, so they are
    # removed from the GICS base and remain unique in the full universe.
    existing=current.get('sectors', {})
    custom={etf:list(tickers) for etf,tickers in existing.items() if etf not in GICS_TO_ETF}
    custom_tickers={ticker for tickers in custom.values() for ticker in tickers}
    for etf in sectors:
        sectors[etf]=[ticker for ticker in sectors[etf] if ticker not in custom_tickers]
    sectors.update(custom)

    updated=copy.deepcopy(current); updated['sectors']=sectors
    updated['universe_metadata']={
        'name':'S&P 500 by GICS plus curated thematic and country lenses',
        'source':'Wikipedia List of S&P 500 companies; curated US-traded thematic and country baskets',
        'source_url':'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies',
        'updated_at':datetime.now(timezone.utc).isoformat(),
        'component_count':sum(len(x) for x in sectors.values()),
    }
    CONFIG_PATH.write_text(json.dumps(updated,ensure_ascii=False,indent=2),encoding='utf-8')
    return updated


if __name__=='__main__':
    result=apply_sp500(fetch_constituents()); print(f"Universe updated: {result['universe_metadata']['component_count']} components")
