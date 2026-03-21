import os
import json
import re
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import requests
import concurrent.futures
from datetime import datetime
import time
import traceback
import math

# เปิดการใช้งาน ANSI Colors บน Windows Terminal
os.system('') 

MAX_THREADS = 7

# 🔑 ใส่ API Key ของ CoinMarketCap Pro ที่นี่ (ถ้ามี)
PRO_API_KEY = os.environ.get('CMC_PRO_API_KEY', '') 

# ==========================================
# 🛠️ Helper Functions
# ==========================================
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

def print_api_log(url, status_code):
    """ฟังก์ชันสำหรับปริ้นท์ Log พร้อมใส่สี ANSI ตาม Status Code"""
    ts = datetime.now().strftime("%d %b %y %H:%M")
    path = url.split('.com')[-1] if '.com' in url else url
    
    # กำหนดสีตาม HTTP Status Code
    if 200 <= status_code < 300:
        color = '\033[92m'  # สีเขียว (Success)
    elif 300 <= status_code < 400:
        color = '\033[93m'  # สีเหลือง (Redirects)
    else:
        color = '\033[91m'  # สีแดง (Errors)
        
    reset = '\033[0m'       # คืนค่าสีเดิม
    
    print(f"{ts} | HTTP {color}{status_code}{reset} | {path}")

def fetch_historical_ohlc(cid, date_added_str):
    try:
        if not date_added_str:
            date_added_str = "2013-04-28T00:00:00.000Z" 
            
        start_ts = int(datetime.strptime(date_added_str, "%Y-%m-%dT%H:%M:%S.000Z").timestamp())
        now_ts = int(time.time())
        
        all_quotes = []
        current_start = start_ts
        
        while current_start < now_ts:
            current_end = min(current_start + (365 * 86400), now_ts)
            url = f"https://api.coinmarketcap.com/data-api/v3.1/cryptocurrency/historical?id={cid}&convertId=2781&timeStart={current_start}&timeEnd={current_end}&interval=1d"
            
            headers = {'User-Agent': 'Mozilla/5.0'}
            res = requests.get(url, headers=headers, timeout=15)
            
            data = res.json().get('data', {})
            quotes = data.get('quotes', []) if data else []
            
            if not quotes:
                break
                
            all_quotes.extend(quotes)
            current_start = current_end + 1
            time.sleep(0.3) 

        if not all_quotes:
            return [""] * 10

        ath_quote = max(all_quotes, key=lambda q: q['quote']['close'])
        atl_quote = min(all_quotes, key=lambda q: q['quote']['close'])

        return [
            ath_quote['quote']['open'],
            ath_quote['quote']['high'],
            ath_quote['quote']['low'],
            ath_quote['quote']['close'],
            format_short_date(ath_quote['timeClose']), 
            atl_quote['quote']['open'],
            atl_quote['quote']['high'],
            atl_quote['quote']['low'],
            atl_quote['quote']['close'],
            format_short_date(atl_quote['timeClose'])  
        ]
    except Exception as e:
        return [""] * 10

# ==========================================
# 🌐 Processing Logic (DEX & Currencies & Untracked)
# ==========================================
def process_dex_url(url):
    ts = datetime.now().strftime("%m/%d/%Y %H:%M:%S")
    headers = {'User-Agent': 'Mozilla/5.0'}
    response = requests.get(url, headers=headers, timeout=15)

    if response.status_code == 404:
        return ["Not Found (404)"] + [""] * 13 + [ts]

    html = response.text
    data = {
        'n': extract_value(html, r'"n":"(.*?)"'),
        'sym': extract_value(html, r'"sym":"(.*?)"'),
        'addr': extract_value(html, r'"addr":"(.*?)"'),
        'pdex': extract_value(html, r'"pdex":"(.*?)"'),
        'ph24h': safe_float(extract_value(html, r'"ph24h":"(.*?)"')),
        'pl24h': safe_float(extract_value(html, r'"pl24h":"(.*?)"')),
        'cslug': extract_value(html, r'"cslug":"(.*?)"'),
        'circulatingSupply': safe_float(extract_value(html, r'"circulatingSupply":\s*([0-9.]+)')),
        'totalSupply': safe_float(extract_value(html, r'"totalSupply":\s*([0-9.]+)')),
        'maxSupply': safe_float(extract_value(html, r'"maxSupply":\s*([0-9.]+)')),
        'lf': safe_float(extract_value(html, r'"lf":\s*(\d+)')),
        'cid': safe_float(extract_value(html, r'"cid":\s*(\d+)')),
        'holdersCount': safe_float(extract_value(html, r'"holdersCount":\s*(\d+)')),
    }
    
    if data['n'] is None and data['sym'] is None:
         return ["Scrape Failed"] + [""] * 13 + [ts]

    result = [
        data['n'] if data['n'] is not None else "",
        data['sym'] if data['sym'] is not None else "",
        data['addr'] if data['addr'] is not None else "",
        data['pdex'] if data['pdex'] is not None else "",
        data['ph24h'] if data['ph24h'] is not None else "",
        data['pl24h'] if data['pl24h'] is not None else "",
        data['cslug'] if data['cslug'] is not None else "",
        data['lf'] if data['lf'] is not None else "",
        data['cid'] if data['cid'] is not None else "",
        data['holdersCount'] if data['holdersCount'] is not None else "",
        ts,
        data['circulatingSupply'] if data['circulatingSupply'] is not None else "",
        data['totalSupply'] if data['totalSupply'] is not None else "",
        data['maxSupply'] if data['maxSupply'] is not None else ""
    ]
    return result

def process_currency_url(url, sheet_address=''):
    ts = datetime.now().strftime("%m/%d/%Y %H:%M:%S")
    slug = get_slug_from_url(url)
    if not slug:
        return ["Invalid URL"] + [""] * 25 + [ts]

    headers = {'User-Agent': 'Mozilla/5.0'}
    
    try:
        detail_url = f"https://api.coinmarketcap.com/data-api/v3/cryptocurrency/detail?slug={slug}"
        detail_res = requests.get(detail_url, headers=headers, timeout=15).json()
        detail = detail_res.get('data', {})
        
        if not detail:
            return ["Not Found API"] + [""] * 25 + [ts]

        cid = detail.get('id', '')
        name = detail.get('name', '')
        symbol = detail.get('symbol', '')
        date_added = detail.get('dateAdded', '')

        # Contract Address from ETC sheet Column C, Network from API
        platforms = detail.get('platforms', [])
        if sheet_address:
            address = sheet_address
            network = platforms[0].get('contractPlatform', '') if platforms else symbol
        else:
            address = f"UCID={cid}"
            network = symbol

        tags_list = detail.get('tags', [])
        tags_str = ", ".join([t.get('name', '') for t in tags_list])

        stats = detail.get('statistics', {})
        
        open_yesterday = stats.get('openYesterday', '')
        high_yesterday = stats.get('highYesterday', '')
        low_yesterday = stats.get('lowYesterday', '')
        close_yesterday = stats.get('closeYesterday', '')
        
        circulating_supply = stats.get('circulatingSupply', '')
        total_supply = stats.get('totalSupply', '')
        max_supply = stats.get('maxSupply', '')

        market_url = f"https://api.coinmarketcap.com/data-api/v3/cryptocurrency/market-pairs/latest?slug={slug}&start=1&limit=50&category=spot"
        market_res = requests.get(market_url, headers=headers, timeout=15).json()
        markets = market_res.get('data', {}).get('marketPairs', [])
        
        exchange_names = []
        for m in markets:
            ex_name = m.get('exchangeName')
            if ex_name and ex_name not in exchange_names:
                exchange_names.append(ex_name)
        exchanges_str = ", ".join(exchange_names[:10])

        historical_data = fetch_historical_ohlc(cid, date_added)

        return [
            name, symbol, address, network, open_yesterday, high_yesterday, low_yesterday, close_yesterday, 
            slug, cid, tags_str, exchanges_str, *historical_data, ts, circulating_supply, total_supply, max_supply
        ]

    except Exception as e:
        return [f"Error: {slug}"] + [""] * 25 + [ts]

def process_any_url(url, index, total, sheet_address=''):
    ts = datetime.now().strftime("%m/%d/%Y %H:%M:%S")
    try:
        current_time = datetime.now().strftime("%d %B %Y")
        percent = math.floor((index / total) * 100)
        
        is_currency = "/currencies/" in url
        req_type = "API (MAIN)" if is_currency else "Scrape (DEX)"
        print(f"{current_time} : ↻ [{req_type}] {percent}% : {url}")

        if is_currency:
            time.sleep(0.5) 
            return ('currency', process_currency_url(url, sheet_address))
        else:
            return ('dex', process_dex_url(url))
            
    except Exception as e:
        if "/currencies/" in url:
            return ('currency', [f"SysError: {str(e)}"] + [""] * 25 + [ts])
        else:
            return ('dex', [f"SysError: {str(e)}"] + [""] * 13 + [ts])

def get_untracked_row(item, pro_api_status):
    ts = datetime.now().strftime("%m/%d/%Y %H:%M:%S")
    slug = item.get('slug', '')
    slug_lower = slug.lower() if slug else ''
    
    # 🚨 กรองข้อมูลที่ไม่มี Slug ทิ้ง ไม่นำไปใช้ต่อ 🚨
    if not slug:
        return None 
        
    headers = {'User-Agent': 'Mozilla/5.0'}
    categories = item.get('tags', '')
    exchanges = item.get('exchanges', '')
    circulating_supply = item.get('circulating_supply', '')
    total_supply = item.get('total_supply', '')
    max_supply = item.get('max_supply', '')
    is_active = 0
    
    try:
        if not categories or slug_lower not in pro_api_status or circulating_supply == '':
            detail_url = f"https://api.coinmarketcap.com/data-api/v3/cryptocurrency/detail?slug={slug}"
            detail_res = requests.get(detail_url, headers=headers, timeout=10)
            print_api_log(detail_url, detail_res.status_code)
            
            detail_data = detail_res.json().get('data', {})
            
            if detail_data:
                if slug_lower not in pro_api_status:
                    status_val = detail_data.get('status')
                    if isinstance(status_val, str):
                        is_active = 1 if status_val.lower() == 'active' else 0
                    elif isinstance(status_val, int):
                        is_active = 1 if status_val == 1 else 0
                    else:
                        is_active = detail_data.get('isActive', detail_data.get('is_active', 0))
                
                if not categories:
                    tags_list = detail_data.get('tags', [])
                    categories = ", ".join([t.get('name', '') for t in tags_list])
                    
                stats = detail_data.get('statistics', {})
                if circulating_supply == '': circulating_supply = stats.get('circulatingSupply', '')
                if total_supply == '': total_supply = stats.get('totalSupply', '')
                if max_supply == '': max_supply = stats.get('maxSupply', '')

        if slug_lower in pro_api_status:
            is_active = pro_api_status[slug_lower]
            
        if not exchanges:
            market_url = f"https://api.coinmarketcap.com/data-api/v3/cryptocurrency/market-pairs/latest?slug={slug}&start=1&limit=50&category=spot"
            market_res = requests.get(market_url, headers=headers, timeout=10)
            print_api_log(market_url, market_res.status_code)
            
            markets = market_res.json().get('data', {}).get('marketPairs', [])
            
            exchange_names = []
            for m in markets:
                ex_name = m.get('exchangeName')
                if ex_name and ex_name not in exchange_names:
                    exchange_names.append(ex_name)
            exchanges = ", ".join(exchange_names[:10])
            
    except Exception as e:
        pass
        
    untracked_bool = True if is_active == 1 else False
    
    return [
        item['name'],       
        item['symbol'],     
        item['address'],    
        item['network'],    
        categories,         
        exchanges,          
        slug,               
        item.get('cid', ''),
        circulating_supply, 
        total_supply,       
        max_supply,         
        untracked_bool,     
        ts                  
    ]

# ==========================================
# 🚀 Main Execution Cycle
# ==========================================
def run_update_cycle():
    start_time = datetime.now()
    current_date = start_time.strftime("%d %B %Y")
    print(f"\n{current_date} : 🚀 Starting Unified Update Cycle...")

    try:
        scope = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive"
        ]
        
        creds_json = os.environ.get('GCP_CREDENTIALS')
        if creds_json:
            print(f"{current_date} : ☁️ Running on Cloud/GitHub Actions...")
            creds = json.loads(creds_json)
        else:
            print(f"{current_date} : 💻 Local environment detected. Loading from 'credentials.json'...")
            try:
                with open('credentials.json', 'r', encoding='utf-8') as file:
                    creds = json.load(file)
            except FileNotFoundError:
                raise FileNotFoundError("⚠️ ไม่พบไฟล์ credentials.json สำหรับรันบนเครื่อง Local")

        credentials = ServiceAccountCredentials.from_json_keyfile_dict(creds, scope)
        client = gspread.authorize(credentials)

        sheet = client.open_by_key("1fQGFO2fmf8wde6Sn9FeCrQK_9ehUujA7REoGBolCImA")
        worksheet = sheet.worksheet("ETC")
        urls = worksheet.get("D2:D")
        addresses = worksheet.get("C2:C")

        if not urls:
            print(f"{current_date} : ⚠️ No URLs found")
            return

        total_urls = len(urls)
        print(f"{current_date} : 🔗 Found {total_urls} URLs total")

        dex_results = [[""] * 14 for _ in range(total_urls)]
        currency_results = [[""] * 26 for _ in range(total_urls)]

        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:
            futures = {}
            for i, url in enumerate(urls):
                if url and len(url) > 0 and url[0].strip():
                    sheet_addr = addresses[i][0].strip() if i < len(addresses) and addresses[i] and len(addresses[i]) > 0 else ''
                    future = executor.submit(process_any_url, url[0].strip(), i+1, total_urls, sheet_addr)
                    futures[future] = i 

            for future in concurrent.futures.as_completed(futures):
                original_index = futures[future]
                result_type, result_data = future.result()
                
                if result_type == 'dex':
                    dex_results[original_index] = result_data
                elif result_type == 'currency':
                    currency_results[original_index] = result_data

        packed_dex = [row for row in dex_results if any(cell != "" for cell in row)]
        packed_currency = [row for row in currency_results if any(cell != "" for cell in row)]

        packed_currency_write = [row[:23] for row in packed_currency]

        dest_sheet = client.open_by_key("1S3KXq6QdTep3RvR97LhURQ8sZzvTaVN1RJVxeWj3IXc")

        # ---------------------------------------------------------
        # ยกเลิกการเขียนชีท ETC ตามที่คุณสั่งแล้วครับ 🗑️
        # ---------------------------------------------------------

        # ---------------------------------------------------------
        # 1. เขียนชีท MAIN
        # ---------------------------------------------------------
        if packed_currency_write:
            try:
                dest_ws_main = dest_sheet.worksheet("MAIN")
            except:
                dest_ws_main = dest_sheet.add_worksheet(title="MAIN", rows="1000", cols="26")
            print(f"{current_date} : 🧹 Clearing old data in [MAIN]...")
            dest_ws_main.batch_clear(["A2:Z"])
            print(f"{current_date} : ✍️ Writing {len(packed_currency_write)} rows to [MAIN]...")
            dest_ws_main.update("A2", packed_currency_write)

        # ---------------------------------------------------------
        # 2. ประมวลผลและเขียนชีท Untracked
        # ---------------------------------------------------------
        print(f"\n{current_date} : 🔍 Compiling data for [Untracked] check...")
        
        all_items = []
        for row in packed_dex:
            slug = row[6]
            if slug and str(slug).strip() != "":
                all_items.append({
                    "name": row[0], "symbol": row[1], "address": row[2], 
                    "network": row[3], "slug": slug, "cid": row[8] if len(row) > 8 else '',
                    "circulating_supply": row[11], "total_supply": row[12], "max_supply": row[13],
                    "type": "dex"
                })
        for row in packed_currency:
            slug = row[8]
            if slug and str(slug).strip() != "":
                all_items.append({
                    "name": row[0], "symbol": row[1], "address": row[2], 
                    "network": row[3], "slug": slug, "cid": row[9],
                    "tags": row[10], "exchanges": row[11], 
                    "circulating_supply": row[23], "total_supply": row[24], "max_supply": row[25],
                    "type": "currency"
                })

        pro_api_status = {}
        if PRO_API_KEY and len(all_items) > 0:
            print(f"{current_date} : 🌐 Fetching is_active from CMC Pro API...")
            try:
                valid_slugs = [i['slug'] for i in all_items if i['slug']]
                chunked_slugs = [valid_slugs[i:i + 50] for i in range(0, len(valid_slugs), 50)]
                for chunk in chunked_slugs:
                    pro_url = f"https://pro-api.coinmarketcap.com/v3/cryptocurrency/quotes/latest?slug={','.join(chunk)}"
                    pro_headers = {'X-CMC_PRO_API_KEY': PRO_API_KEY}
                    
                    res = requests.get(pro_url, headers=pro_headers)
                    print_api_log(pro_url, res.status_code)
                    
                    data = res.json().get('data', {})
                    for cid, info in data.items():
                        slug_key = info.get('slug', '').lower()
                        if slug_key:
                            pro_api_status[slug_key] = info.get('is_active', 0)
            except Exception as e:
                print(f"⚠️ Pro API Error: {str(e)}")

        print(f"{current_date} : ↻ Gathering Categories, Exchanges & Supply...")
        untracked_results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:
            futures = [executor.submit(get_untracked_row, item, pro_api_status) for item in all_items]
            for future in futures:
                result = future.result()
                if result: 
                    untracked_results.append(result)

        if untracked_results:
            try:
                dest_ws_untracked = dest_sheet.worksheet("Untracked")
            except:
                dest_ws_untracked = dest_sheet.add_worksheet(title="Untracked", rows="1000", cols="15")

            print(f"{current_date} : 🧹 Clearing old data in [Untracked]...")
            dest_ws_untracked.batch_clear(["A2:Z"])
            print(f"{current_date} : ✍️ Writing {len(untracked_results)} rows to [Untracked]...")
            dest_ws_untracked.update("A2", untracked_results)

        elapsed = datetime.now() - start_time
        print(f"\n{current_date} : ✅ All Cycles Completed in {elapsed.total_seconds():.2f}s")

    except Exception as e:
        print(f"{current_date} : ❌ Critical Error: {str(e)}")
        print(traceback.format_exc())

def main():
    print("=== CMC Unified Auto-Updater (MAIN + Untracked) ===")
    run_update_cycle()

if __name__ == "__main__":
    main()