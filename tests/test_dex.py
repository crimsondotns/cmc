import json

from dex import DexTokenRow, is_dex_url, parse_dex_page


def _page(token, supply=None, holders=None):
    queries = [{"queryKey": ["dex-token-info", "ethereum", token.get("addr", "")],
                "state": {"data": token}}]
    if supply is not None:
        queries.append({"queryKey": ["dex-token-all-networks-info", 1], "state": {"data": supply}})
    data = {"props": {"pageProps": {"holdersCount": holders,
                                     "dehydratedState": {"queries": queries}}}}
    return f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>'


def test_is_dex_url():
    assert is_dex_url("https://dex.coinmarketcap.com/token/ethereum/0xabc/")
    assert not is_dex_url("https://coinmarketcap.com/currencies/bitcoin/")
    assert not is_dex_url("https://coinmarketcap.com/dexscan/ethereum/0xabc")


def _orbio_page():
    token = {
        "n": "Orbio.so", "sym": "ORBIO", "plt": "Robinhood Chain", "pdex": "Robinhood",
        "addr": "0xaa07", "dec": 18, "crt": "0xbfab", "web": "https://www.orbio.so/",
        "tw": "https://twitter.com/orbiodotso", "tg": "", "lg": "https://logo",
        "pubAt": "1788215591000", "lchAt": "1791380556000",
        "p": "0.5", "ph24h": "0.6", "pl24h": "0.4", "liqUsd": "1000", "mcap": "2000", "ts": "100",
        "sts": [{"tp": "5m", "vu": "1", "txs": "2", "pc": 0.01},
                {"tp": "24h", "vu": "99", "txs": "7", "nb": "4", "ns": "3", "bvu": "60",
                 "svu": "39", "but": "2", "sut": "1", "ut": "3", "pc": -0.06}],
        "pls": [{"addr": "0xsmall", "liqUsd": "10", "exn": "Uniswap v4 (Robinhood)",
                 "t0": {"sym": "USDG"}, "t1": {"sym": "ORBIO"}},
                {"addr": "0xbig", "liqUsd": "1019936.08", "exn": "Uniswap v4 (Robinhood)",
                 "t0": {"sym": "USDG"}, "t1": {"sym": "ORBIO"}}],
        "cexs": [{"n": "Gate", "cat": ["DERIVATIVES"]}, {"n": "LBank", "cat": ["SPOT"]},
                 {"n": "KCEX", "cat": ["SPOT", "DERIVATIVES"]}],
        "rl": "safe", "pshr": "NONE", "cslug": "orbio-so", "cid": 42040,
    }
    supply = {"circulatingSupply": 10, "totalSupply": 20, "maxSupply": 30,
              "selfReportedCirculatingSupply": "950000000.00"}
    page = _page(token, supply, 555)
    security = {"queryKey": ["dex-token-security-info"], "state": {"data": {
        "evmDisplay": {"honeypotStatus": "No", "unverifiedContractStatus": "No",
                       "rugPullStatus": "Unknown", "fakeTokenStatus": "No"},
        "extra": {"buyTax": "0.0", "sellTax": "0.05"}}}}
    return page.replace('"queries": [', '"queries": [' + json.dumps(security) + ", ", 1)


def test_parse_dex_page():
    row = parse_dex_page(_orbio_page(), "URL", "TS")
    assert (row.name, row.chain, row.network, row.decimals) == ("Orbio.so", "Robinhood Chain", "Robinhood", 18.0)
    assert (row.website, row.telegram) == ("https://www.orbio.so/", "")
    assert row.created_at == "2026-08-31 22:33"
    assert (row.price_usd, row.total_supply, row.fdv) == (0.5, 20.0, 10.0)
    assert row.self_reported_supply == 950000000.0 and row.holders == 555.0
    # สถิติแยกช่วงเวลา: ช่วงที่ไม่มีข้อมูล (1h, 4h) เป็นช่องว่าง
    assert row.stats_24h.to_row() == [-0.06, 99.0, 7.0, 4.0, 3.0, 60.0, 39.0, 2.0, 1.0, 3.0]
    assert row.stats_5m.volume == 1.0 and row.stats_1h.volume == ""
    # pool เรียงตาม liquidity มาก → น้อย
    assert (row.pool_count, row.top_pool, row.top_pool_liquidity) == (2, "0xbig", 1019936.08)
    assert row.pool_dexes == "Uniswap v4 (Robinhood)"
    assert row.pools == ("ORBIO/USDG · Uniswap v4 (Robinhood) · $1,019,936 | "
                         "ORBIO/USDG · Uniswap v4 (Robinhood) · $10")
    assert (row.cex_count, row.cex_spot, row.cex_derivatives) == (3, "LBank, KCEX", "Gate, KCEX")
    assert (row.honeypot, row.rug_pull, row.mintable, row.sell_tax) == ("No", "Unknown", "", 0.05)
    assert (row.cmc_slug, row.cmc_id, row.updated_at) == ("orbio-so", 42040, "TS")


def test_parse_dex_page_solana_security():
    page = _page({"n": "W", "sym": "W"}).replace('"queries": [', '"queries": [' + json.dumps(
        {"queryKey": ["dex-token-security-info"], "state": {"data": {"solanaDisplay": {
            "mintableStatus": "No", "freezableStatus": "Unknown"}}}}) + ", ", 1)
    row = parse_dex_page(page, "URL", "TS")
    assert (row.mintable, row.freezable, row.honeypot) == ("No", "Unknown", "")


def test_parse_dex_page_minimal_token():
    row = parse_dex_page(_page({"n": "X", "sym": "X"}), "URL", "TS")
    assert (row.name, row.price_usd, row.top_pool, row.holders, row.pool_count) == ("X", "", "", "", "")
    assert len(row.to_row()) == len(DexTokenRow.headers())


def test_parse_dex_page_failures():
    assert parse_dex_page("<html></html>", "URL", "TS").name == "Scrape Failed"
    assert parse_dex_page(_page({}), "URL", "TS").name == "Scrape Failed"


def test_dex_headers():
    headers = DexTokenRow.headers()
    assert len(headers) == len(DexTokenRow().to_row()) == 86
    assert headers[:4] == ["Name", "Symbol", "Chain", "Network"]
    assert "24h Volume (USD)" in headers and "5m Traders" in headers
    assert len(set(headers)) == len(headers)  # ไม่มีหัวซ้ำ


def test_missing_cmc_id_is_blank_not_zero():
    row = parse_dex_page(_page({"n": "X", "sym": "X", "cid": 0, "cslug": None}), "URL", "TS")
    assert (row.cmc_id, row.cmc_slug) == ("", "")
