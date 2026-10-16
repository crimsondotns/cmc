# CMC Unified Auto-Updater

ดึงข้อมูลเหรียญจาก CoinMarketCap ตามรายการ URL ใน Google Sheets แล้วเขียนผลกลับลงอีกชีต
รันอัตโนมัติผ่าน GitHub Actions ทุกวัน: `UCID Update` 07:00 น. และ `DEX Scrape` 08:00 น. (เวลาไทย)

## การทำงาน

**อ่าน** — ชีต `SOURCE_SHEET_ID` แท็บ `CoinmarketCap`
| คอลัมน์ | ข้อมูล |
|---|---|
| G (G2:G) | contract address (ถ้ากรอก จะใช้แทน `UCID=<id>`) |
| I (I2:I) | URL ของ CMC — หน้าเหรียญ `/currencies/<slug>` หรือหน้า DEX |

**ประมวลผล**
- URL หน้าเหรียญ → API ภายในเว็บ CMC (`detail`, `market-pairs`, `historical`) ได้ข้อมูลเหรียญ, exchanges และราคา ATH/ATL
- URL หน้า DEX → อ่านค่าจาก HTML ของหน้านั้น
- เช็กสถานะ active ผ่าน CMC Pro API (`v3/cryptocurrency/quotes/latest`) ถ้าตั้ง `CMC_PRO_API_KEY` ไว้

**เขียน** — ชีต `DEST_SHEET_ID`: หัวคอลัมน์ที่แถว 1, ข้อมูลตั้งแต่แถว 2 (เขียนทับก่อน แล้วล้างแถวเก่าที่เหลือ)
- ถ้าแถว error เกิน `MAX_ERROR_RATIO` (20%) จะไม่เขียนทับ เก็บข้อมูลเดิมไว้ และ workflow ขึ้นสีแดง
- แถวที่ URL ในชีตใช้ไม่ได้ (`Not Found (404)`, `Not Found API`, `Invalid URL`) ไม่ถูกเขียน แต่ขึ้น ERROR ใน log

| แท็บ | เนื้อหา |
|---|---|
| `CEX` | 1 แถวต่อ URL หน้าเหรียญ, 23 คอลัมน์ (ดู `CurrencyRow` ใน `main.py`) |
| `Token Registry` | ทุกรายการที่มี slug ทั้งหน้าเหรียญและ DEX, 13 คอลัมน์ (ดู `RegistryRow`) |

ลำดับคอลัมน์ในชีตเท่ากับลำดับ field ใน dataclass — ถ้าจะเพิ่ม/สลับคอลัมน์ ให้แก้ที่ dataclass

ID ชีต, ชื่อแท็บ และช่วงเซลล์ ตั้งค่าได้ที่หัวข้อ `Config` บนสุดของ `main.py`

## DEX Scraper (`dex.py`)

สคริปต์แยก (workflow `DEX Scrape`) — เลือกเฉพาะ URL ของ `dex.coinmarketcap.com` จากคอลัมน์ I แท็บ `CoinmarketCap`
อ่านข้อมูลจาก JSON `__NEXT_DATA__` ในหน้าเว็บ แล้วเขียนลงแท็บ `DEX` ของชีตปลายทาง 1 แถวต่อ URL
— 86 คอลัมน์ (ดู `DexTokenRow` ใน `dex.py`):
- ข้อมูลโปรเจกต์: ชื่อ, symbol, chain, network, address, decimals, creator, เว็บ, Twitter, Telegram, โลโก้, วันที่สร้าง/launch (UTC)
- ราคา/มูลค่า: ราคา, high/low 24h, liquidity, market cap, FDV, supply (รวม self-reported), holders
- การซื้อขายแยก 5m / 1h / 4h / 24h: % เปลี่ยน, volume, txs, จำนวนซื้อ/ขาย, volume ซื้อ/ขาย, จำนวนคนซื้อ/ขาย, traders
- pool (รวมเป็นข้อความ เรียงตาม liquidity), CEX ที่ list แยก Spot/Derivatives
- ความปลอดภัย: honeypot, unverified, mintable, freezable, rug pull, fake token, buy/sell tax, pump status
- CMC slug/id, URL, เวลาอัปเดต

```bash
python dex.py
```

## Secrets (GitHub → Settings → Secrets and variables → Actions)

| Secret | จำเป็น | คำอธิบาย |
|---|---|---|
| `GCP_CREDENTIALS` | ✅ | JSON ของ Google service account (ต้องแชร์ทั้งสองชีตให้อีเมลของ service account นี้) |
| `CMC_PRO_API_KEY` | ไม่บังคับ | API key ของ CMC Pro ใช้เช็กสถานะ active ถ้าไม่ใส่จะใช้สถานะจาก API ภายในเว็บแทน |

## รันบนเครื่อง

```bash
pip install -r requirements.txt
# วาง credentials.json (service account) ไว้ในโฟลเดอร์นี้ หรือ export GCP_CREDENTIALS='<json>'
export CMC_PRO_API_KEY=...   # ไม่บังคับ
python main.py
```

## Test

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest
```
