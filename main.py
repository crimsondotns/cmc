import os
import json
import re
# pyrefly: ignore [missing-import]
import gspread
import requests
import concurrent.futures
from dataclasses import dataclass, field, fields
from typing import ClassVar
from datetime import datetime
from urllib.parse import urlparse
import time
# pyrefly: ignore [missing-import]
import sys
import traceback

from reporting import report

# ==========================================
# ⚙️ Config
# ==========================================
MAX_THREADS = 7

# 🔑 ใส่ API Key ของ CoinMarketCap Pro ที่นี่ (ถ้ามี)
PRO_API_KEY = os.environ.get('CMC_PRO_API_KEY', '')

# 📥 ชีตต้นทาง
SOURCE_SHEET_ID = "1Yk2vMgJEQEidZ0D7EjQMZcRDXwaMghtTQhEMvsOCpsE"
SOURCE_WORKSHEET = "CoinmarketCap"
SOURCE_URL_RANGE = "I2:I"
SOURCE_ADDRESS_RANGE = "G2:G"

# 📤 ชีตปลายทาง
DEST_SHEET_ID = "1S3KXq6QdTep3RvR97LhURQ8sZzvTaVN1RJVxeWj3IXc"
DEST_MAIN_WORKSHEET = "CEX"
DEST_UNTRACKED_WORKSHEET = "Untracked"

HEADERS = {'User-Agent': 'Mozilla/5.0'}

# ชื่อแถวเมื่อหน้าเว็บตอบ 404
NOT_FOUND = "Not Found (404)"

# ลองโหลดหน้า DEX ซ้ำกี่ครั้ง เมื่อได้หน้าที่ไม่มีข้อมูล token (มักเป็นหน้ากันบอท/โหลดไม่ครบ)
SCRAPE_RETRIES = 3

# จำนวนครั้งที่ยิง request เมื่อเจอ 429/5xx/timeout (404 และ 4xx อื่นไม่ retry)
HTTP_RETRIES = 3

# ถ้าแถว error เกินสัดส่วนนี้ จะไม่เขียนทับชีต (เก็บข้อมูลเดิมไว้) และจบด้วย exit 1
MAX_ERROR_RATIO = 0.2

# ==========================================
# 📋 Row Models (ลำดับ field = ลำดับคอลัมน์ในชีต)
# ==========================================
@dataclass
class SheetRow:
    """แปลง dataclass เป็นแถวของชีต ตามลำดับการประกาศ field

    - field ที่ metadata col=False ไม่ถูกเขียนลงชีต
    - field ที่เป็น SheetRow ซ้อนอยู่ จะถูกกระจายเป็นหลายคอลัมน์
    - หัวคอลัมน์มาจาก LABELS (ถ้าไม่มี ใช้ชื่อ field)
    error: เหตุผลที่ดึงข้อมูลไม่สำเร็จ ใช้นับใน report
    """
    LABELS: ClassVar[dict] = {}

    error: str = field(default="", repr=False, compare=False, metadata={'col': False})
    # True = URL ในชีตใช้ไม่ได้ (404 / Not Found API / Invalid URL): ไม่เขียนลงชีต และไม่นับในเกณฑ์ error
    bad_url: bool = field(default=False, repr=False, compare=False, metadata={'col': False})

    @classmethod
    def _columns(cls):
        return [f for f in fields(cls) if f.metadata.get('col', True)]

    @staticmethod
    def _nested(f):
        factory = f.default_factory
        return factory if isinstance(factory, type) and issubclass(factory, SheetRow) else None

    @classmethod
    def headers(cls):
        out = []
        for f in cls._columns():
            nested = cls._nested(f)
            out.extend(nested.headers() if nested else [cls.LABELS.get(f.name, f.name)])
        return out

    def to_row(self):
        out = []
        for f in self._columns():
            value = getattr(self, f.name)
            out.extend(value.to_row() if isinstance(value, SheetRow) else [value])
        return out

    @classmethod
    def failed(cls, reason, ts, label=None, bad_url=False):
        """แถว error: ใส่ข้อความใน name และเวลาใน updated_at"""
        return cls(name=label or reason, updated_at=ts, error=reason, bad_url=bad_url)


@dataclass
class OhlcSummary(SheetRow):
    LABELS: ClassVar[dict] = {
        'ath_open': "ATH Open", 'ath_high': "ATH High", 'ath_low': "ATH Low",
        'ath_close': "ATH Close", 'ath_date': "ATH Date",
        'atl_open': "ATL Open", 'atl_high': "ATL High", 'atl_low': "ATL Low",
        'atl_close': "ATL Close", 'atl_date': "ATL Date",
    }

    ath_open: object = ""
    ath_high: object = ""
    ath_low: object = ""
    ath_close: object = ""
    ath_date: str = ""
    atl_open: object = ""
    atl_high: object = ""
    atl_low: object = ""
    atl_close: object = ""
    atl_date: str = ""


@dataclass
class DexRow(SheetRow):
    """ข้อมูลจากหน้า DEX (ไม่ได้เขียนลงชีต ใช้เป็นต้นทางของ Untracked)"""
    name: str = ""
    symbol: str = ""
    address: str = ""
    network: str = ""
    high_24h: object = ""
    low_24h: object = ""
    slug: str = ""
    lf: object = ""
    cid: object = ""
    holders_count: object = ""
    updated_at: str = ""
    circulating_supply: object = ""
    total_supply: object = ""
    max_supply: object = ""


@dataclass
class CurrencyRow(SheetRow):
    """1 แถวของชีต CEX (supply ไม่ถูกเขียนลงชีต ใช้ส่งต่อให้ Untracked)"""
    LABELS: ClassVar[dict] = {
        'name': "Name", 'symbol': "Symbol", 'address': "Address", 'network': "Network",
        'open_yesterday': "Open (เมื่อวาน)", 'high_yesterday': "High (เมื่อวาน)",
        'low_yesterday': "Low (เมื่อวาน)", 'close_yesterday': "Close (เมื่อวาน)",
        'slug': "Slug", 'cid': "CMC ID", 'tags': "Tags", 'exchanges': "Exchanges",
        'updated_at': "Updated At",
    }

    name: str = ""
    symbol: str = ""
    address: str = ""
    network: str = ""
    open_yesterday: object = ""
    high_yesterday: object = ""
    low_yesterday: object = ""
    close_yesterday: object = ""
    slug: str = ""
    cid: object = ""
    tags: str = ""
    exchanges: str = ""
    ohlc: OhlcSummary = field(default_factory=OhlcSummary)
    updated_at: str = ""
    circulating_supply: object = field(default="", metadata={'col': False})
    total_supply: object = field(default="", metadata={'col': False})
    max_supply: object = field(default="", metadata={'col': False})


@dataclass
class UntrackedRow(SheetRow):
    """1 แถวของชีต Untracked"""
    LABELS: ClassVar[dict] = {
        'name': "Name", 'symbol': "Symbol", 'address': "Address", 'network': "Network",
        'categories': "Categories", 'exchanges': "Exchanges", 'slug': "Slug", 'cid': "CMC ID",
        'circulating_supply': "Circulating Supply", 'total_supply': "Total Supply",
        'max_supply': "Max Supply", 'is_active': "Active", 'updated_at': "Updated At",
    }

    name: str = ""
    symbol: str = ""
    address: str = ""
    network: str = ""
    categories: str = ""
    exchanges: str = ""
    slug: str = ""
    cid: object = ""
    circulating_supply: object = ""
    total_supply: object = ""
    max_supply: object = ""
    is_active: bool = False
    updated_at: str = ""

    @classmethod
    def from_source(cls, row):
        """สร้างจาก DexRow หรือ CurrencyRow โดยคัดลอก field ที่ชื่อตรงกัน"""
        return cls(
            name=row.name,
            symbol=row.symbol,
            address=row.address,
            network=row.network,
            categories=getattr(row, 'tags', ''),
            exchanges=getattr(row, 'exchanges', ''),
            slug=row.slug,
            cid=row.cid,
            circulating_supply=row.circulating_supply,
            total_supply=row.total_supply,
            max_supply=row.max_supply,
        )

# ==========================================
# 🛠️ Helper Functions
# ==========================================
def now_ts():
    return datetime.now().strftime("%m/%d/%Y %H:%M:%S")

def extract_value(text, regex):
    if not text:
        return None
    try:
        match = re.search(regex, text)
        return match.group(1).strip() if match else None
    except:
        return None

def safe_float(val):
    try:
        num = float(val)
        return None if num == 0 else num
    except:
        return None

def url_label(url):
    """ชื่อสั้นของ URL สำหรับ log: currencies/<slug> -> slug, token/<chain>/<addr> -> chain/addr"""
    parts = [p for p in urlparse(url).path.split('/') if p]
    if len(parts) >= 2 and parts[0] == 'currencies':
        return parts[1]
    if len(parts) >= 3 and parts[0] == 'token':
        return f"{parts[1]}/{parts[2]}"
    return url.removeprefix('https://')

def describe_page(res):
    """สรุปหน้าเว็บที่ scrape ไม่ได้ สำหรับใส่ในข้อความ error"""
    title = extract_value(res.text, r'<title[^>]*>(.*?)</title>') or '-'
    return f"HTTP {res.status_code}, {len(res.content) // 1024}KB, title={title[:60]!r}"

def not_found_reason(url):
    """เหตุผลของ 404 พร้อมคำใบ้ถ้า address ดูผิดรูปแบบ"""
    reason = "Not Found (404): CMC ไม่มีหน้านี้"
    parts = [p for p in urlparse(url).path.split('/') if p]
    if len(parts) >= 3 and parts[0] == 'token':
        addr = parts[2]
        if addr.startswith('0x') and '::' not in addr and len(addr) != 42:
            return f"{reason} — address ยาว {len(addr)} ตัว (EVM ต้อง 42) ตรวจในชีต"
    return f"{reason} — ตรวจ URL/address ในชีต"

def get_slug_from_url(url):
    parts = [p for p in url.split('/') if p]
    if "currencies" in parts:
        idx = parts.index("currencies")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    return None

def format_short_date(iso_str):
    if not iso_str:
        return ""
    try:
        date_part = iso_str.split('T')[0]
        dt = datetime.strptime(date_part, "%Y-%m-%d")
        return dt.strftime("%d %b %y")
    except:
        return iso_str.split('T')[0]

def unique_exchange_names(market_pairs, limit=10):
    names = []
    for m in market_pairs:
        ex_name = m.get('exchangeName')
        if ex_name and ex_name not in names:
            names.append(ex_name)
    return ", ".join(names[:limit])

def summarize_ohlc(quotes):
    if not quotes:
        return OhlcSummary()
    ath = max(quotes, key=lambda q: q['quote']['close'])
    atl = min(quotes, key=lambda q: q['quote']['close'])
    return OhlcSummary(
        ath_open=ath['quote']['open'],
        ath_high=ath['quote']['high'],
        ath_low=ath['quote']['low'],
        ath_close=ath['quote']['close'],
        ath_date=format_short_date(ath['timeClose']),
        atl_open=atl['quote']['open'],
        atl_high=atl['quote']['high'],
        atl_low=atl['quote']['low'],
        atl_close=atl['quote']['close'],
        atl_date=format_short_date(atl['timeClose']),
    )

class HttpError(Exception):
    pass

def backoff(attempt):
    time.sleep(2 ** attempt * 2)

def http_get(url, headers=HEADERS, timeout=15):
    """GET พร้อม retry เมื่อเจอ 429/5xx/timeout/เชื่อมต่อไม่ได้ (404 และ 4xx อื่นคืนทันที)"""
    for attempt in range(HTTP_RETRIES):
        last = attempt == HTTP_RETRIES - 1
        try:
            res = requests.get(url, headers=headers, timeout=timeout)
        except requests.RequestException as e:
            if last:
                raise
            report.warn(f"{type(e).__name__} retry {attempt + 1}/{HTTP_RETRIES - 1}: {urlparse(url).path}")
            backoff(attempt)
            continue
        if res.status_code != 429 and res.status_code < 500 or last:
            return res
        report.warn(f"HTTP {res.status_code} retry {attempt + 1}/{HTTP_RETRIES - 1}: {urlparse(url).path}")
        backoff(attempt)

def get_data(url, timeout=15):
    """เรียก JSON API ของ CMC แล้วคืนค่า data ({} ถ้าเป็น null) — status ไม่ใช่ 200 หรือไม่ใช่ JSON จะ raise HttpError"""
    res = http_get(url, timeout=timeout)
    path = urlparse(url).path
    if res.status_code != 200:
        raise HttpError(f"HTTP {res.status_code} {path}")
    try:
        body = res.json()
    except ValueError:
        raise HttpError(f"ไม่ใช่ JSON {path} ({describe_page(res)})")
    return (body or {}).get('data') or {}

def fetch_historical_ohlc(cid, date_added_str):
    """คืน (OhlcSummary, error) — error ไม่ว่างถ้าดึงประวัติไม่ครบ (ไม่คำนวณ ATH/ATL จากข้อมูลบางส่วน)"""
    try:
        date_added = (date_added_str or "2013-04-28T00:00:00.000Z").replace('Z', '+00:00')
        start_ts = int(datetime.fromisoformat(date_added).timestamp())
        end_ts = int(time.time())

        all_quotes = []
        current_start = start_ts

        while current_start < end_ts:
            current_end = min(current_start + (365 * 86400), end_ts)
            url = f"https://api.coinmarketcap.com/data-api/v3.1/cryptocurrency/historical?id={cid}&convertId=2781&timeStart={current_start}&timeEnd={current_end}&interval=1d"
            quotes = get_data(url).get('quotes') or []

            if not quotes:
                break

            all_quotes.extend(quotes)
            current_start = current_end + 1
            time.sleep(0.3)

        return summarize_ohlc(all_quotes), ""
    except Exception as e:
        return OhlcSummary(), f"ohlc: {type(e).__name__}: {e}"

# ==========================================
# 🌐 Processing Logic (DEX & Currencies & Untracked)
# ==========================================
def parse_dex_html(html, ts):
    def text(regex):
        return extract_value(html, regex) or ""

    def number(regex):
        value = safe_float(extract_value(html, regex))
        return "" if value is None else value

    row = DexRow(
        name=text(r'"n":"(.*?)"'),
        symbol=text(r'"sym":"(.*?)"'),
        address=text(r'"addr":"(.*?)"'),
        network=text(r'"pdex":"(.*?)"'),
        high_24h=number(r'"ph24h":"(.*?)"'),
        low_24h=number(r'"pl24h":"(.*?)"'),
        slug=text(r'"cslug":"(.*?)"'),
        lf=number(r'"lf":\s*(\d+)'),
        cid=number(r'"cid":\s*(\d+)'),
        holders_count=number(r'"holdersCount":\s*(\d+)'),
        updated_at=ts,
        circulating_supply=number(r'"circulatingSupply":\s*([0-9.]+)'),
        total_supply=number(r'"totalSupply":\s*([0-9.]+)'),
        max_supply=number(r'"maxSupply":\s*([0-9.]+)'),
    )
    if not row.name and not row.symbol:
        return DexRow.failed("Scrape Failed", ts)
    return row

def process_dex_url(url):
    for attempt in range(SCRAPE_RETRIES):
        ts = now_ts()
        response = http_get(url)

        if response.status_code == 404:
            return DexRow.failed(not_found_reason(url), ts, label=NOT_FOUND, bad_url=True)
        if response.status_code != 200:
            return DexRow.failed(f"HTTP {response.status_code} ({describe_page(response)})", ts,
                                 label=f"HTTP {response.status_code}")

        row = parse_dex_html(response.text, ts)
        if not row.error:
            return row
        if attempt < SCRAPE_RETRIES - 1:
            backoff(attempt)

    row.error = f"Scrape Failed หลังลอง {SCRAPE_RETRIES} ครั้ง — ไม่พบชื่อ/symbol ในหน้า ({describe_page(response)})"
    return row

def build_currency_row(detail, slug, sheet_address, exchanges, ohlc, ts):
    cid = detail.get('id', '')
    symbol = detail.get('symbol', '')

    # Contract Address from ETC sheet Column C, Network from API
    platforms = detail.get('platforms') or []
    if sheet_address:
        address = sheet_address
        network = platforms[0].get('contractPlatform', '') if platforms else symbol
    else:
        address = f"UCID={cid}"
        network = symbol

    stats = detail.get('statistics') or {}
    return CurrencyRow(
        name=detail.get('name', ''),
        symbol=symbol,
        address=address,
        network=network,
        open_yesterday=stats.get('openYesterday', ''),
        high_yesterday=stats.get('highYesterday', ''),
        low_yesterday=stats.get('lowYesterday', ''),
        close_yesterday=stats.get('closeYesterday', ''),
        slug=slug,
        cid=cid,
        tags=", ".join([t.get('name', '') for t in detail.get('tags') or []]),
        exchanges=exchanges,
        ohlc=ohlc,
        updated_at=ts,
        circulating_supply=stats.get('circulatingSupply', ''),
        total_supply=stats.get('totalSupply', ''),
        max_supply=stats.get('maxSupply', ''),
    )

def process_currency_url(url, sheet_address=''):
    ts = now_ts()
    slug = get_slug_from_url(url)
    if not slug:
        return CurrencyRow.failed("Invalid URL: หา slug จาก URL ไม่ได้", ts, label="Invalid URL", bad_url=True)

    try:
        detail_url = f"https://api.coinmarketcap.com/data-api/v3/cryptocurrency/detail?slug={slug}"
        detail = get_data(detail_url)

        if not detail:
            return CurrencyRow.failed("Not Found API: detail API ไม่มีข้อมูลของ slug นี้ — ตรวจ URL ในชีต", ts, label="Not Found API", bad_url=True)

        # ส่วนที่ดึงไม่สำเร็จ: ยังเขียนข้อมูลที่ได้ แต่นับแถวเป็น error
        errors = []
        market_url = f"https://api.coinmarketcap.com/data-api/v3/cryptocurrency/market-pairs/latest?slug={slug}&start=1&limit=50&category=spot"
        try:
            exchanges = unique_exchange_names(get_data(market_url).get('marketPairs') or [])
        except Exception as e:
            exchanges = ""
            errors.append(f"market-pairs: {type(e).__name__}: {e}")

        ohlc, ohlc_error = fetch_historical_ohlc(detail.get('id', ''), detail.get('dateAdded', ''))
        if ohlc_error:
            errors.append(ohlc_error)

        row = build_currency_row(detail, slug, sheet_address, exchanges, ohlc, ts)
        row.error = "; ".join(errors)
        return row

    except Exception as e:
        return CurrencyRow.failed(f"{type(e).__name__}: {e}", ts, label=f"Error: {slug}")

def process_any_url(url, sheet_address=''):
    is_currency = "/currencies/" in url
    try:
        if is_currency:
            time.sleep(0.5)
            return process_currency_url(url, sheet_address)
        else:
            return process_dex_url(url)

    except Exception as e:
        row_cls = CurrencyRow if is_currency else DexRow
        return row_cls.failed(f"{type(e).__name__}: {e}", now_ts(), label=f"SysError: {str(e)}")

def fetch_pro_api_status(slugs):
    """คืน dict {slug: is_active} จาก CMC Pro API"""
    status = {}
    if not PRO_API_KEY or not slugs:
        return status

    for i in range(0, len(slugs), 50):
        chunk = slugs[i:i + 50]
        pro_url = f"https://pro-api.coinmarketcap.com/v3/cryptocurrency/quotes/latest?slug={','.join(chunk)}"
        try:
            res = http_get(pro_url, headers={'X-CMC_PRO_API_KEY': PRO_API_KEY})
            if res.status_code != 200:
                report.warn(f"Pro API HTTP {res.status_code} (slug ที่ {i + 1}-{i + len(chunk)})")
                continue
            data = (res.json() or {}).get('data') or {}
            # รองรับทั้งแบบ dict {id: info | [info]} และแบบ list [info]
            for info in (data.values() if isinstance(data, dict) else data):
                for entry in (info if isinstance(info, list) else [info]):
                    slug_key = (entry.get('slug') or '').lower()
                    if slug_key:
                        status[slug_key] = entry.get('is_active', 0)
        except Exception as e:
            report.warn(f"Pro API (slug ที่ {i + 1}-{i + len(chunk)}): {type(e).__name__}: {e}")
    return status

def get_untracked_row(source_row, pro_api_status):
    if not source_row.slug:
        return None

    row = UntrackedRow.from_source(source_row)
    row.updated_at = now_ts()
    slug_lower = row.slug.lower()
    is_active = 0

    try:
        if not row.categories or slug_lower not in pro_api_status or row.circulating_supply == '':
            detail_url = f"https://api.coinmarketcap.com/data-api/v3/cryptocurrency/detail?slug={row.slug}"
            detail_data = get_data(detail_url, timeout=10)

            if detail_data:
                if slug_lower not in pro_api_status:
                    status_val = detail_data.get('status')
                    if isinstance(status_val, str):
                        is_active = 1 if status_val.lower() == 'active' else 0
                    elif isinstance(status_val, int):
                        is_active = 1 if status_val == 1 else 0
                    else:
                        is_active = detail_data.get('isActive', detail_data.get('is_active', 0))

                if not row.categories:
                    row.categories = ", ".join([t.get('name', '') for t in detail_data.get('tags') or []])

                stats = detail_data.get('statistics') or {}
                if row.circulating_supply == '': row.circulating_supply = stats.get('circulatingSupply', '')
                if row.total_supply == '': row.total_supply = stats.get('totalSupply', '')
                if row.max_supply == '': row.max_supply = stats.get('maxSupply', '')

        if slug_lower in pro_api_status:
            is_active = pro_api_status[slug_lower]

        if not row.exchanges:
            market_url = f"https://api.coinmarketcap.com/data-api/v3/cryptocurrency/market-pairs/latest?slug={row.slug}&start=1&limit=50&category=spot"
            row.exchanges = unique_exchange_names(get_data(market_url, timeout=10).get('marketPairs') or [])

    except Exception as e:
        row.error = f"{type(e).__name__}: {e}"

    row.is_active = is_active == 1
    return row

# ==========================================
# 📊 Google Sheets I/O
# ==========================================
def get_gspread_client():
    creds_json = os.environ.get('GCP_CREDENTIALS')
    if creds_json:
        return gspread.service_account_from_dict(json.loads(creds_json))

    if not os.path.exists('credentials.json'):
        raise FileNotFoundError("ไม่พบไฟล์ credentials.json สำหรับรันบนเครื่อง Local")
    return gspread.service_account(filename='credentials.json')

def read_source(client):
    """คืน list ของ (url, sheet_address) จากชีตต้นทาง"""
    worksheet = client.open_by_key(SOURCE_SHEET_ID).worksheet(SOURCE_WORKSHEET)
    urls = worksheet.get(SOURCE_URL_RANGE)
    addresses = worksheet.get(SOURCE_ADDRESS_RANGE)

    entries = []
    for i, url in enumerate(urls):
        url_val = url[0].strip() if url else ''
        addr_val = addresses[i][0].strip() if i < len(addresses) and addresses[i] else ''
        entries.append((url_val, addr_val))
    return entries

def drop_bad_urls(rows):
    """ตัดแถวที่ URL ในชีตใช้ไม่ได้ออก (ยังขึ้น ERROR ใน log ไปแล้วตอนดึง)"""
    return [r for r in rows if not r.bad_url]

def can_write(title, rows):
    """เช็กก่อนเขียนทับ: ถ้าแถว error เกิน MAX_ERROR_RATIO ไม่เขียน (กันข้อมูลดีถูกทับด้วยแถว error)"""
    if not rows:
        return False
    failed = sum(1 for r in rows if r.error)
    if failed / len(rows) > MAX_ERROR_RATIO:
        report.fail("write", title, f"error {failed}/{len(rows)} ({failed / len(rows):.0%}) "
                                    f"เกินเกณฑ์ {MAX_ERROR_RATIO:.0%} — ไม่เขียนทับ เก็บข้อมูลเดิมไว้")
        return False
    return True

def write_worksheet(dest_sheet, title, header, rows):
    """เขียนหัวคอลัมน์ที่แถว 1 + ข้อมูลตั้งแต่แถว 2 แล้วค่อยล้างแถวเก่าที่เหลือด้านล่าง

    เขียนก่อนล้าง: ถ้าการเขียนพัง ข้อมูลเดิมยังอยู่ (ล้างเฉพาะคอลัมน์ A ถึงคอลัมน์สุดท้ายของ header)
    """
    needed_rows, needed_cols = len(rows) + 1, len(header)
    try:
        ws = dest_sheet.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = dest_sheet.add_worksheet(title=title, rows=max(1000, needed_rows), cols=needed_cols)
    if ws.row_count < needed_rows:
        ws.add_rows(needed_rows - ws.row_count)
    if ws.col_count < needed_cols:
        ws.add_cols(needed_cols - ws.col_count)

    ws.update([header] + rows, "A1")
    if ws.row_count > needed_rows:
        last_col = gspread.utils.rowcol_to_a1(1, needed_cols).rstrip('0123456789')
        ws.batch_clear([f"A{needed_rows + 1}:{last_col}"])
    ws.freeze(rows=1)
    report.info(f"เขียน [{title}] {len(rows)} แถว")

# ==========================================
# 🚀 Main Execution Cycle
# ==========================================
def record(stage, row, label):
    if row.error:
        report.fail(stage, label, row.error)
    else:
        report.ok(stage)

def fetch_all(entries):
    """ดึงข้อมูลทุก URL แบบขนาน คืน (dex_rows, currency_rows) ตามลำดับในชีต"""
    jobs = [(i, url, addr) for i, (url, addr) in enumerate(entries) if url]
    results = [None] * len(entries)

    with report.progress("Fetching", len(jobs)) as advance, \
            concurrent.futures.ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:
        futures = {executor.submit(process_any_url, url, addr): (i, url) for i, url, addr in jobs}
        for future in concurrent.futures.as_completed(futures):
            i, url = futures[future]
            row = future.result()
            results[i] = row
            stage = "currency" if isinstance(row, CurrencyRow) else "dex"
            label = url_label(url)
            record(stage, row, label)
            advance(label)

    dex_rows = [r for r in results if isinstance(r, DexRow)]
    currency_rows = [r for r in results if isinstance(r, CurrencyRow)]
    return dex_rows, currency_rows

def build_untracked_rows(dex_rows, currency_rows):
    sources = [r for r in dex_rows + currency_rows if r.slug and str(r.slug).strip()]
    pro_api_status = fetch_pro_api_status([r.slug for r in sources])

    rows = []
    with report.progress("Untracked", len(sources)) as advance, \
            concurrent.futures.ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:
        for row in executor.map(lambda r: get_untracked_row(r, pro_api_status), sources):
            if row:
                record("untracked", row, row.slug)
                rows.append(row)
            advance(row.slug if row else "")
    return rows

def run_update_cycle():
    """คืน True ถ้ารอบนี้จบโดยไม่มี error ร้ายแรง"""
    try:
        client = get_gspread_client()

        # 1. อ่าน
        entries = read_source(client)
        if not entries:
            report.warn("ไม่พบ URL ในชีตต้นทาง")
            return True
        report.info(f"พบ {len(entries)} URL")

        # 2. ประมวลผล
        dex_rows, currency_rows = fetch_all(entries)
        untracked_rows = build_untracked_rows(dex_rows, currency_rows)

        # 3. เขียน
        dest_sheet = client.open_by_key(DEST_SHEET_ID)
        ok = True
        for title, cls, rows in ((DEST_MAIN_WORKSHEET, CurrencyRow, currency_rows),
                                 (DEST_UNTRACKED_WORKSHEET, UntrackedRow, untracked_rows)):
            rows = drop_bad_urls(rows)
            if not rows:
                continue
            if can_write(title, rows):
                write_worksheet(dest_sheet, title, cls.headers(), [r.to_row() for r in rows])
            else:
                ok = False
        return ok

    except Exception as e:
        report.fail("cycle", "Critical Error", str(e))
        report.info(traceback.format_exc())
        return False

def main():
    # เปิดการใช้งาน ANSI Colors บน Windows Terminal
    os.system('')
    ok = run_update_cycle()
    report.summary("CMC Unified Auto-Updater (MAIN + Untracked)")
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
