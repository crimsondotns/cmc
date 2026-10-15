import os
import sys
import json
import re
import traceback
import concurrent.futures
from dataclasses import dataclass
from typing import ClassVar
from urllib.parse import urlparse

from main import (
    DEST_SHEET_ID, NOT_FOUND, SCRAPE_RETRIES, SheetRow, backoff, can_write, describe_page, drop_bad_urls,
    get_gspread_client, http_get, not_found_reason, now_ts, read_source, record, url_label,
    write_worksheet,
)
from reporting import report

# ==========================================
# ⚙️ Config
# ==========================================
MAX_THREADS = 5
DEX_HOST = "dex.coinmarketcap.com"
DEST_DEX_WORKSHEET = "DEX"

NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)

# ==========================================
# 📋 Row Model (ลำดับ field = ลำดับคอลัมน์ในแท็บ DEX)
# ==========================================
@dataclass
class DexTokenRow(SheetRow):
    LABELS: ClassVar[dict] = {
        'name': "Name", 'symbol': "Symbol", 'network': "Network", 'address': "Address",
        'price_usd': "Price (USD)", 'high_24h': "High 24h", 'low_24h': "Low 24h",
        'price_change_24h': "% Change 24h", 'volume_24h': "Volume 24h (USD)", 'txs_24h': "Txs 24h",
        'liquidity_usd': "Liquidity (USD)", 'market_cap': "Market Cap",
        'circulating_supply': "Circulating Supply", 'total_supply': "Total Supply",
        'max_supply': "Max Supply", 'holders': "Holders", 'security': "Security",
        'top_pool': "Top Pool", 'cmc_slug': "CMC Slug", 'cmc_id': "CMC ID", 'url': "URL",
        'updated_at': "Updated At",
    }

    name: str = ""
    symbol: str = ""
    network: str = ""
    address: str = ""
    price_usd: object = ""
    high_24h: object = ""
    low_24h: object = ""
    price_change_24h: object = ""
    volume_24h: object = ""
    txs_24h: object = ""
    liquidity_usd: object = ""
    market_cap: object = ""
    circulating_supply: object = ""
    total_supply: object = ""
    max_supply: object = ""
    holders: object = ""
    security: str = ""
    top_pool: str = ""
    cmc_slug: str = ""
    cmc_id: object = ""
    url: str = ""
    updated_at: str = ""

# ==========================================
# 🛠️ Helpers
# ==========================================
def is_dex_url(url):
    return urlparse(url).netloc.lower() == DEX_HOST

def to_number(val):
    if val is None or val == "":
        return ""
    try:
        return float(val)
    except (TypeError, ValueError):
        return ""

def find_query(queries, key):
    """หา data ของ react-query ตามชื่อ queryKey[0]"""
    for q in queries:
        query_key = q.get('queryKey') or []
        if query_key and query_key[0] == key:
            return (q.get('state') or {}).get('data') or {}
    return {}

def parse_dex_page(html, url, ts):
    match = NEXT_DATA_RE.search(html)
    if not match:
        return DexTokenRow(name="Scrape Failed", url=url, updated_at=ts, error="ไม่พบ __NEXT_DATA__ ในหน้า")

    try:
        props = (json.loads(match.group(1)).get('props') or {}).get('pageProps') or {}
    except ValueError:
        return DexTokenRow(name="Scrape Failed", url=url, updated_at=ts, error="__NEXT_DATA__ ไม่ใช่ JSON ที่สมบูรณ์")
    queries = (props.get('dehydratedState') or {}).get('queries') or []
    token = find_query(queries, 'dex-token-info')
    if not token.get('n') and not token.get('sym'):
        return DexTokenRow(name="Scrape Failed", url=url, updated_at=ts, error="ไม่พบข้อมูล token ใน __NEXT_DATA__")

    supply = find_query(queries, 'dex-token-all-networks-info')
    stats_24h = next((s for s in token.get('sts') or [] if s.get('tp') == '24h'), {})
    pools = token.get('pls') or []

    return DexTokenRow(
        name=token.get('n', ''),
        symbol=token.get('sym', ''),
        network=token.get('pdex', ''),
        address=token.get('addr', ''),
        price_usd=to_number(token.get('p')),
        high_24h=to_number(token.get('ph24h')),
        low_24h=to_number(token.get('pl24h')),
        price_change_24h=to_number(stats_24h.get('pc')),
        volume_24h=to_number(stats_24h.get('vu')),
        txs_24h=to_number(stats_24h.get('txs')),
        liquidity_usd=to_number(token.get('liqUsd')),
        market_cap=to_number(token.get('mcap')),
        circulating_supply=to_number(supply.get('circulatingSupply') or token.get('ltcs')),
        total_supply=to_number(supply.get('totalSupply') or token.get('ts')),
        max_supply=to_number(supply.get('maxSupply')),
        holders=to_number(props.get('holdersCount')),
        security=token.get('rl', ''),
        top_pool=pools[0].get('addr', '') if pools else '',
        cmc_slug=token.get('cslug') or '',
        cmc_id=token.get('cid') or '',  # 0 = ไม่มีใน CMC → เว้นว่าง
        url=url,
        updated_at=ts,
    )

def scrape_dex_url(url):
    ts = now_ts()
    try:
        for attempt in range(SCRAPE_RETRIES):
            res = http_get(url)
            if res.status_code == 404:
                return DexTokenRow(name=NOT_FOUND, url=url, updated_at=ts, bad_url=True,
                                   error=not_found_reason(url))
            if res.status_code != 200:
                reason = f"HTTP {res.status_code} ({describe_page(res)})"
                return DexTokenRow(name=f"HTTP {res.status_code}", url=url, updated_at=ts, error=reason)

            row = parse_dex_page(res.text, url, ts)
            if not row.error:
                return row
            if attempt < SCRAPE_RETRIES - 1:
                backoff(attempt)

        row.error = f"Scrape Failed หลังลอง {SCRAPE_RETRIES} ครั้ง — {row.error} ({describe_page(res)})"
        return row
    except Exception as e:
        reason = f"{type(e).__name__}: {e}"
        return DexTokenRow(name=f"SysError: {str(e)}", url=url, updated_at=ts, error=reason)

# ==========================================
# 🚀 Main
# ==========================================
def run_dex_cycle():
    """คืน True ถ้ารอบนี้จบโดยไม่มี error ร้ายแรง"""
    try:
        client = get_gspread_client()

        # 1. อ่าน
        urls = [url for url, _ in read_source(client) if url and is_dex_url(url)]
        if not urls:
            report.warn(f"ไม่พบ URL ของ {DEX_HOST}")
            return True
        report.info(f"พบ {len(urls)} DEX URL")

        # 2. ประมวลผล (เรียงตามลำดับในชีต)
        rows = []
        with report.progress("DEX", len(urls)) as advance, \
                concurrent.futures.ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:
            for url, row in zip(urls, executor.map(scrape_dex_url, urls)):
                label = url_label(url)
                record("dex", row, label)
                rows.append(row)
                advance(label)

        # 3. เขียน (ไม่เขียนแถว 404 และไม่นับรวมในเกณฑ์ error เพราะเป็นปัญหา URL ในชีต ไม่ใช่โดนบล็อก)
        rows = drop_bad_urls(rows)
        if not rows:
            report.warn("ทุก URL เป็น 404 — ไม่เขียนชีต")
            return True
        if not can_write(DEST_DEX_WORKSHEET, rows):
            return False
        dest_sheet = client.open_by_key(DEST_SHEET_ID)
        write_worksheet(dest_sheet, DEST_DEX_WORKSHEET, DexTokenRow.headers(),
                        [r.to_row() for r in rows])
        return True

    except Exception as e:
        report.fail("cycle", "Critical Error", str(e))
        report.info(traceback.format_exc())
        return False

def main():
    # เปิดการใช้งาน ANSI Colors บน Windows Terminal
    os.system('')
    ok = run_dex_cycle()
    report.summary("CMC DEX Scraper")
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
