import os
import sys
import json
import re
import traceback
import concurrent.futures
from dataclasses import dataclass, field
from datetime import datetime, timezone
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
TIMEFRAMES = ("5m", "1h", "4h", "24h")


@dataclass
class TradeStats(SheetRow):
    """สถิติการซื้อขายของ 1 ช่วงเวลา (จาก token.sts)"""
    LABELS: ClassVar[dict] = {
        'change_pct': "% Change", 'volume': "Volume (USD)", 'txs': "Txs",
        'buys': "Buys", 'sells': "Sells", 'buy_volume': "Buy Volume (USD)",
        'sell_volume': "Sell Volume (USD)", 'buyers': "Buyers", 'sellers': "Sellers",
        'traders': "Traders",
    }

    change_pct: object = ""
    volume: object = ""
    txs: object = ""
    buys: object = ""
    sells: object = ""
    buy_volume: object = ""
    sell_volume: object = ""
    buyers: object = ""
    sellers: object = ""
    traders: object = ""

    @classmethod
    def from_sts(cls, s):
        return cls(
            change_pct=to_number(s.get('pc')), volume=to_number(s.get('vu')),
            txs=to_number(s.get('txs')), buys=to_number(s.get('nb')), sells=to_number(s.get('ns')),
            buy_volume=to_number(s.get('bvu')), sell_volume=to_number(s.get('svu')),
            buyers=to_number(s.get('but')), sellers=to_number(s.get('sut')),
            traders=to_number(s.get('ut')),
        )


def stats_field(tf):
    return field(default_factory=TradeStats, metadata={'prefix': f"{tf} "})


@dataclass
class DexTokenRow(SheetRow):
    LABELS: ClassVar[dict] = {
        # ข้อมูลโปรเจกต์
        'name': "Name", 'symbol': "Symbol", 'chain': "Chain", 'network': "Network",
        'address': "Address", 'decimals': "Decimals", 'creator': "Creator",
        'website': "Website", 'twitter': "Twitter", 'telegram': "Telegram", 'logo': "Logo",
        'created_at': "Created At (UTC)", 'launched_at': "Launched At (UTC)",
        # ราคา / มูลค่า
        'price_usd': "Price (USD)", 'high_24h': "High 24h", 'low_24h': "Low 24h",
        'liquidity_usd': "Liquidity (USD)", 'market_cap': "Market Cap", 'fdv': "FDV",
        'circulating_supply': "Circulating Supply", 'total_supply': "Total Supply",
        'max_supply': "Max Supply", 'self_reported_supply': "Self-reported Supply",
        'holders': "Holders",
        # pool / CEX
        'pool_count': "Pools", 'pool_dexes': "Pool DEXes", 'pools': "Pools Detail",
        'top_pool': "Top Pool", 'top_pool_liquidity': "Top Pool Liquidity (USD)",
        'cex_count': "CEX Count", 'cex_spot': "CEX (Spot)", 'cex_derivatives': "CEX (Derivatives)",
        # ความปลอดภัย
        'security': "Security", 'honeypot': "Honeypot", 'unverified_contract': "Unverified Contract",
        'mintable': "Mintable", 'freezable': "Freezable", 'rug_pull': "Rug Pull",
        'fake_token': "Fake Token", 'buy_tax': "Buy Tax", 'sell_tax': "Sell Tax",
        'pump_status': "Pump Status",
        # อ้างอิง
        'cmc_slug': "CMC Slug", 'cmc_id': "CMC ID", 'url': "URL", 'updated_at': "Updated At",
    }

    # ข้อมูลโปรเจกต์
    name: str = ""
    symbol: str = ""
    chain: str = ""
    network: str = ""
    address: str = ""
    decimals: object = ""
    creator: str = ""
    website: str = ""
    twitter: str = ""
    telegram: str = ""
    logo: str = ""
    created_at: str = ""
    launched_at: str = ""
    # ราคา / มูลค่า
    price_usd: object = ""
    high_24h: object = ""
    low_24h: object = ""
    liquidity_usd: object = ""
    market_cap: object = ""
    fdv: object = ""
    circulating_supply: object = ""
    total_supply: object = ""
    max_supply: object = ""
    self_reported_supply: object = ""
    holders: object = ""
    # การซื้อขายแยกช่วงเวลา (คอลัมน์ละ 10 ค่า × 4 ช่วง)
    stats_5m: TradeStats = stats_field("5m")
    stats_1h: TradeStats = stats_field("1h")
    stats_4h: TradeStats = stats_field("4h")
    stats_24h: TradeStats = stats_field("24h")
    # pool / CEX (รายการรวมเป็นข้อความเดียว)
    pool_count: object = ""
    pool_dexes: str = ""
    pools: str = ""
    top_pool: str = ""
    top_pool_liquidity: object = ""
    cex_count: object = ""
    cex_spot: str = ""
    cex_derivatives: str = ""
    # ความปลอดภัย (EVM: honeypot/unverified, Solana: mintable/freezable)
    security: str = ""
    honeypot: str = ""
    unverified_contract: str = ""
    mintable: str = ""
    freezable: str = ""
    rug_pull: str = ""
    fake_token: str = ""
    buy_tax: object = ""
    sell_tax: object = ""
    pump_status: str = ""
    # อ้างอิง
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

def ms_to_utc(ms):
    """timestamp มิลลิวินาที → 'YYYY-MM-DD HH:MM' (UTC)"""
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return ""

def join(values):
    """รวมรายการเป็นข้อความเดียว ตัดตัวซ้ำ คงลำดับเดิม"""
    return ", ".join(dict.fromkeys(v for v in values if v))

def find_query(queries, key):
    """หา data ของ react-query ตามชื่อ queryKey[0]"""
    for q in queries:
        query_key = q.get('queryKey') or []
        if query_key and query_key[0] == key:
            return (q.get('state') or {}).get('data') or {}
    return {}

def describe_pool(pool):
    """'ORBIO/USDG · Uniswap v4 (Robinhood) · $1,019,936'"""
    pair = "/".join(((pool.get(t) or {}).get('sym') or '?') for t in ('t1', 't0'))
    liq = to_number(pool.get('liqUsd'))
    liq_text = f"${liq:,.0f}" if liq != "" else "-"
    return f"{pair} · {pool.get('exn') or '?'} · {liq_text}"

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
    security = find_query(queries, 'dex-token-security-info')
    display = security.get('evmDisplay') or security.get('solanaDisplay') or {}
    extra = security.get('extra') or {}
    stats = {s.get('tp'): s for s in token.get('sts') or []}

    pools = sorted(token.get('pls') or [], key=lambda p: to_number(p.get('liqUsd')) or 0, reverse=True)
    cexs = token.get('cexs') or []

    price = to_number(token.get('p'))
    total_supply = to_number(supply.get('totalSupply') or token.get('ts'))

    return DexTokenRow(
        name=token.get('n') or '',
        symbol=token.get('sym') or '',
        chain=token.get('plt') or '',
        network=token.get('pdex') or '',
        address=token.get('addr') or '',
        decimals=to_number(token.get('dec')),
        creator=token.get('crt') or '',
        website=token.get('web') or '',
        twitter=token.get('tw') or '',
        telegram=token.get('tg') or '',
        logo=token.get('lg') or '',
        created_at=ms_to_utc(token.get('pubAt')),
        launched_at=ms_to_utc(token.get('lchAt')),
        price_usd=price,
        high_24h=to_number(token.get('ph24h')),
        low_24h=to_number(token.get('pl24h')),
        liquidity_usd=to_number(token.get('liqUsd')),
        market_cap=to_number(token.get('mcap')),
        fdv=price * total_supply if price != "" and total_supply != "" else "",
        circulating_supply=to_number(supply.get('circulatingSupply') or token.get('ltcs')),
        total_supply=total_supply,
        max_supply=to_number(supply.get('maxSupply')),
        self_reported_supply=to_number(supply.get('selfReportedCirculatingSupply')),
        holders=to_number(props.get('holdersCount')),
        stats_5m=TradeStats.from_sts(stats.get('5m') or {}),
        stats_1h=TradeStats.from_sts(stats.get('1h') or {}),
        stats_4h=TradeStats.from_sts(stats.get('4h') or {}),
        stats_24h=TradeStats.from_sts(stats.get('24h') or {}),
        pool_count=len(pools) if pools else "",
        pool_dexes=join(p.get('exn') for p in pools),
        pools=" | ".join(describe_pool(p) for p in pools),
        top_pool=pools[0].get('addr', '') if pools else '',
        top_pool_liquidity=to_number(pools[0].get('liqUsd')) if pools else '',
        cex_count=len(cexs) if cexs else "",
        cex_spot=join(c.get('n') for c in cexs if 'SPOT' in (c.get('cat') or [])),
        cex_derivatives=join(c.get('n') for c in cexs if 'DERIVATIVES' in (c.get('cat') or [])),
        security=token.get('rl') or '',
        honeypot=display.get('honeypotStatus') or '',
        unverified_contract=display.get('unverifiedContractStatus') or '',
        mintable=display.get('mintableStatus') or '',
        freezable=display.get('freezableStatus') or '',
        rug_pull=display.get('rugPullStatus') or '',
        fake_token=display.get('fakeTokenStatus') or '',
        buy_tax=to_number(extra.get('buyTax')),
        sell_tax=to_number(extra.get('sellTax')),
        pump_status=token.get('pshr') or '',
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
