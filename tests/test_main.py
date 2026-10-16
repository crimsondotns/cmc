import json

import main
from main import (
    CurrencyRow, DexRow, OhlcSummary, RegistryRow,
    build_currency_row, get_slug_from_url, parse_dex_html, safe_float,
    summarize_ohlc, unique_exchange_names, format_short_date,
)


def test_get_slug_from_url():
    assert get_slug_from_url("https://coinmarketcap.com/currencies/bitcoin/") == "bitcoin"
    assert get_slug_from_url("https://coinmarketcap.com/dexscan/ethereum/0xabc") is None
    assert get_slug_from_url("https://coinmarketcap.com/currencies/") is None


def test_safe_float():
    assert safe_float("1.5") == 1.5
    assert safe_float("0") is None
    assert safe_float(None) is None
    assert safe_float("abc") is None


def test_format_short_date():
    assert format_short_date("2024-03-05T23:59:59.999Z") == "05 Mar 24"
    assert format_short_date("") == ""


def test_unique_exchange_names_dedupes_and_limits():
    pairs = [{"exchangeName": f"EX{i % 12}"} for i in range(30)] + [{"exchangeName": None}]
    assert unique_exchange_names(pairs) == ", ".join(f"EX{i}" for i in range(10))


def _quote(close, day):
    return {"quote": {"open": close - 1, "high": close + 1, "low": close - 2, "close": close},
            "timeClose": f"2024-01-{day:02d}T23:59:59.999Z"}


def test_summarize_ohlc():
    s = summarize_ohlc([_quote(10, 1), _quote(50, 2), _quote(5, 3)])
    assert s.to_row() == [49, 51, 48, 50, "02 Jan 24", 4, 6, 3, 5, "03 Jan 24"]
    assert summarize_ohlc([]).to_row() == [""] * 10


def test_currency_main_row_layout():
    detail = {
        "id": 1, "name": "Bitcoin", "symbol": "BTC",
        "platforms": [{"contractPlatform": "Ethereum"}],
        "tags": [{"name": "PoW"}, {"name": "Store of Value"}],
        "statistics": {"openYesterday": 1, "highYesterday": 2, "lowYesterday": 3,
                       "closeYesterday": 4, "circulatingSupply": 100,
                       "totalSupply": 200, "maxSupply": 300},
    }
    ohlc = summarize_ohlc([_quote(10, 1)])
    row = build_currency_row(detail, "bitcoin", "0xabc", "Binance", ohlc, "TS")
    main_row = row.to_row()

    # ชีต MAIN มี 23 คอลัมน์ (A–W) ไม่รวม supply
    assert len(main_row) == 23
    assert main_row[:12] == ["Bitcoin", "BTC", "0xabc", "Ethereum", 1, 2, 3, 4,
                             "bitcoin", 1, "PoW, Store of Value", "Binance"]
    assert main_row[12:22] == ohlc.to_row()
    assert main_row[22] == "TS"


def test_currency_address_fallback_to_ucid():
    row = build_currency_row({"id": 7, "symbol": "XYZ"}, "xyz", "", "", OhlcSummary(), "TS")
    assert (row.address, row.network) == ("UCID=7", "XYZ")


def test_error_rows_keep_column_count():
    assert len(CurrencyRow(name="Invalid URL", updated_at="TS").to_row()) == 23
    assert len(DexRow(name="Scrape Failed").to_row()) == 14


def test_parse_dex_html():
    html = ('{"n":"Pepe","sym":"PEPE","addr":"0xpepe","pdex":"Uniswap","ph24h":"2.5",'
            '"pl24h":"1.5","cslug":"pepe","lf": 1,"cid": 24478,"holdersCount": 99,'
            '"circulatingSupply": 10,"totalSupply": 20,"maxSupply": 0}')
    row = parse_dex_html(html, "TS")
    assert row.to_row() == ["Pepe", "PEPE", "0xpepe", "Uniswap", 2.5, 1.5, "pepe",
                            1.0, 24478.0, 99.0, "TS", 10.0, 20.0, ""]


def test_parse_dex_html_failure():
    assert parse_dex_html("<html></html>", "TS").name == "Scrape Failed"


def test_registry_row_from_currency_and_dex():
    cur = CurrencyRow(name="A", symbol="A", address="x", network="n", slug="a", cid=1,
                      tags="t", exchanges="e", circulating_supply=1, total_supply=2, max_supply=3)
    u = RegistryRow.from_source(cur)
    u.is_active, u.updated_at = True, "TS"
    assert u.to_row() == ["A", "A", "x", "n", "t", "e", "a", 1, 1, 2, 3, True, "TS"]

    dex = DexRow(name="D", slug="d")
    assert (RegistryRow.from_source(dex).categories, RegistryRow.from_source(dex).exchanges) == ("", "")


def test_get_registry_row_skips_missing_slug():
    assert main.get_registry_row(DexRow(name="x"), {}) is None


def test_format_thai_duration():
    from reporting import format_thai_duration
    assert format_thai_duration(1106) == "18 นาที 26 วินาที"
    assert format_thai_duration(3900) == "1 ชั่วโมง 5 นาที"
    assert format_thai_duration(42.7) == "42 วินาที"


def test_url_label():
    from main import url_label
    assert url_label("https://coinmarketcap.com/currencies/bitcoin/") == "bitcoin"
    assert url_label("https://dex.coinmarketcap.com/token/ethereum/0x3bd7d4f524d09f4e331577247a048d56e4b67a7f/") \
        == "ethereum/0x3bd7d4f524d09f4e331577247a048d56e4b67a7f"
    assert url_label("https://coinmarketcap.com/dexscan/ethereum/0xabc") == "coinmarketcap.com/dexscan/ethereum/0xabc"


def test_not_found_reason_hints_short_evm_address():
    from main import not_found_reason
    short = not_found_reason("https://dex.coinmarketcap.com/token/hyperevm/0x0d01dc56dcaaca66ad901c959b4011ec/")
    assert "ยาว 34 ตัว" in short
    ok = not_found_reason("https://dex.coinmarketcap.com/token/aptos/0x26::loon::loon/")
    assert "ยาว" not in ok and "ตรวจ URL" in ok


class _Res:
    def __init__(self, status, text):
        self.status_code, self.text, self.content = status, text, text.encode()

    def json(self):
        return json.loads(self.text)


def test_process_dex_url_retries_then_explains(monkeypatch):
    calls = []
    monkeypatch.setattr(main.time, "sleep", lambda s: None)
    monkeypatch.setattr(main.requests, "get",
                        lambda *a, **k: calls.append(1) or _Res(200, "<title>Just a moment...</title>"))
    row = main.process_dex_url("https://dex.coinmarketcap.com/token/base/0xabc/")
    assert len(calls) == main.SCRAPE_RETRIES
    assert row.name == "Scrape Failed"
    assert "HTTP 200" in row.error and "Just a moment..." in row.error


def test_process_dex_url_recovers_on_retry(monkeypatch):
    pages = iter([_Res(200, "<html></html>"), _Res(200, '{"n":"Carlo","sym":"CARLO"}')])
    monkeypatch.setattr(main.time, "sleep", lambda s: None)
    monkeypatch.setattr(main.requests, "get", lambda *a, **k: next(pages))
    row = main.process_dex_url("https://dex.coinmarketcap.com/token/base/0xabc/")
    assert (row.name, row.error) == ("Carlo", "")


def test_headers_match_row_length():
    assert len(CurrencyRow.headers()) == len(CurrencyRow().to_row()) == 23
    assert CurrencyRow.headers()[12] == "ATH Open" and CurrencyRow.headers()[-1] == "Updated At"
    assert len(RegistryRow.headers()) == len(RegistryRow().to_row()) == 13
    assert "error" not in CurrencyRow.headers()


# ---------- robustness ----------
import pytest
import requests


class _JsonRes(_Res):
    def __init__(self, status, body):
        super().__init__(status, json.dumps(body))
        self._body = body

    def json(self):
        return self._body



@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(main.time, "sleep", lambda s: None)


def _queue(monkeypatch, responses):
    calls = []
    def fake_get(url, **kwargs):
        calls.append(url)
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item
    monkeypatch.setattr(main.requests, "get", fake_get)
    return calls


def test_http_get_does_not_retry_404(monkeypatch, no_sleep):
    calls = _queue(monkeypatch, [_Res(404, "")])
    assert main.http_get("https://x.com/a").status_code == 404
    assert len(calls) == 1


def test_http_get_retries_429_and_timeouts(monkeypatch, no_sleep):
    calls = _queue(monkeypatch, [_Res(429, ""), requests.Timeout("slow"), _Res(200, "ok")])
    assert main.http_get("https://x.com/a").status_code == 200
    assert len(calls) == 3


def test_http_get_gives_up_after_retries(monkeypatch, no_sleep):
    _queue(monkeypatch, [_Res(503, "")] * main.HTTP_RETRIES)
    assert main.http_get("https://x.com/a").status_code == 503


def test_dex_404_not_retried(monkeypatch, no_sleep):
    calls = _queue(monkeypatch, [_Res(404, "")])
    row = main.process_dex_url("https://dex.coinmarketcap.com/token/base/0xabc/")
    assert row.name == "Not Found (404)" and len(calls) == 1


def test_get_data_handles_null_and_non_json(monkeypatch, no_sleep):
    _queue(monkeypatch, [_JsonRes(200, {"data": None})])
    assert main.get_data("https://x.com/a") == {}
    _queue(monkeypatch, [_Res(200, "<html>blocked</html>")])
    with pytest.raises(main.HttpError):
        main.get_data("https://x.com/a")


def test_ohlc_partial_failure_is_error_not_partial_ath(monkeypatch, no_sleep):
    quotes = {"data": {"quotes": [{"quote": {"open": 1, "high": 2, "low": 0.5, "close": 1.5},
                                   "timeClose": "2024-01-01T23:59:59.999Z"}]}}
    _queue(monkeypatch, [_JsonRes(200, quotes)] + [_Res(429, "")] * main.HTTP_RETRIES)
    summary, error = main.fetch_historical_ohlc(1, "2023-01-01T00:00:00.000Z")
    assert summary == OhlcSummary() and "HTTP 429" in error


def test_can_write_threshold():
    good = [CurrencyRow(name="A")] * 9
    assert main.can_write("CEX", good + [CurrencyRow(error="x")])          # 10% ok
    assert not main.can_write("CEX", good[:7] + [CurrencyRow(error="x")] * 3)  # 30% blocked
    assert not main.can_write("CEX", [])


class _FakeWs:
    def __init__(self, rows, cols):
        self.row_count, self.col_count, self.calls = rows, cols, []
    def add_rows(self, n): self.row_count += n; self.calls.append(("add_rows", n))
    def add_cols(self, n): self.col_count += n; self.calls.append(("add_cols", n))
    def update(self, values, rng): self.calls.append(("update", rng, len(values)))
    def batch_clear(self, ranges): self.calls.append(("clear", ranges))
    def freeze(self, rows): self.calls.append(("freeze", rows))


class _FakeSheet:
    def __init__(self, ws): self.ws = ws
    def worksheet(self, title): return self.ws


def test_write_worksheet_writes_before_clearing_leftovers():
    ws = _FakeWs(rows=1000, cols=26)
    main.write_worksheet(_FakeSheet(ws), "CEX", ["a", "b", "c"], [[1, 2, 3]] * 5)
    assert ws.calls[0] == ("update", "A1", 6)
    assert ws.calls[1] == ("clear", ["A7:C"])


def test_write_worksheet_grows_grid():
    ws = _FakeWs(rows=1000, cols=2)
    main.write_worksheet(_FakeSheet(ws), "CEX", ["a", "b", "c"], [[1, 2, 3]] * 1293)
    assert ("add_rows", 294) in ws.calls and ("add_cols", 1) in ws.calls
    assert not any(c[0] == "clear" for c in ws.calls)


def test_bad_url_rows_are_dropped_before_write():
    rows = [CurrencyRow(name="A"),
            CurrencyRow.failed("x", "TS", label="Not Found API", bad_url=True),
            CurrencyRow.failed("y", "TS", label="Invalid URL", bad_url=True),
            CurrencyRow.failed("blocked", "TS")]
    kept = main.drop_bad_urls(rows)
    assert [r.name for r in kept] == ["A", "blocked"]
    assert "bad_url" not in CurrencyRow.headers()


def test_registry_cmc_id_blank_when_dex_cid_zero():
    row = parse_dex_html('{"n":"X","sym":"X","cid": 0}', "TS")
    assert row.cid == "" and RegistryRow.from_source(row).to_row()[7] == ""
