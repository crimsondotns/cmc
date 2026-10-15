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


def test_parse_dex_page():
    token = {"n": "Pepe", "sym": "PEPE", "pdex": "Ethereum", "addr": "0xpepe",
             "p": "0.5", "ph24h": "0.6", "pl24h": "0.4", "liqUsd": "1000", "mcap": "2000",
             "sts": [{"tp": "1h", "vu": "1"}, {"tp": "24h", "vu": "99", "txs": "7", "pc": -0.06}],
             "pls": [{"addr": "0xpool"}, {"addr": "0xother"}],
             "rl": "safe", "cslug": "pepe", "cid": 24478}
    supply = {"circulatingSupply": 10, "totalSupply": 20, "maxSupply": 30}
    row = parse_dex_page(_page(token, supply, 555), "URL", "TS")

    assert row == DexTokenRow(
        name="Pepe", symbol="PEPE", network="Ethereum", address="0xpepe",
        price_usd=0.5, high_24h=0.6, low_24h=0.4, price_change_24h=-0.06,
        volume_24h=99.0, txs_24h=7.0, liquidity_usd=1000.0, market_cap=2000.0,
        circulating_supply=10.0, total_supply=20.0, max_supply=30.0, holders=555.0,
        security="safe", top_pool="0xpool", cmc_slug="pepe", cmc_id=24478,
        url="URL", updated_at="TS",
    )


def test_parse_dex_page_minimal_token():
    row = parse_dex_page(_page({"n": "X", "sym": "X"}), "URL", "TS")
    assert (row.name, row.price_usd, row.top_pool, row.holders) == ("X", "", "", "")
    assert len(row.to_row()) == 22


def test_parse_dex_page_failures():
    assert parse_dex_page("<html></html>", "URL", "TS").name == "Scrape Failed"
    assert parse_dex_page(_page({}), "URL", "TS").name == "Scrape Failed"


def test_dex_headers():
    headers = DexTokenRow.headers()
    assert len(headers) == len(DexTokenRow().to_row()) == 22
    assert headers[:4] == ["Name", "Symbol", "Network", "Address"]


def test_missing_cmc_id_is_blank_not_zero():
    row = parse_dex_page(_page({"n": "X", "sym": "X", "cid": 0, "cslug": None}), "URL", "TS")
    assert (row.cmc_id, row.cmc_slug) == ("", "")
