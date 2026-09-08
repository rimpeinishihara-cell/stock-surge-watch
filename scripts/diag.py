import requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept-Language": "ja,en;q=0.9",
}

URLS = [
    "https://kabutan.jp/stock/news?code=2330",
    "https://kabutan.jp/stock/?code=2330",
    "https://finance.yahoo.co.jp/stocks/ranking/priceIncreaseRate?market=all&term=daily",
    "https://finance.yahoo.co.jp/quote/2330.T/forum",
    "https://search.yahoo.co.jp/realtime/search/%E3%83%86%E3%82%B9%E3%83%88",
    "https://minkabu.jp/enquete_ranking/rise",
]

for url in URLS:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        waf = resp.headers.get("x-amzn-waf-action", "")
        print(f"{resp.status_code} waf={waf!r} len={len(resp.text)} {url}")
    except Exception as e:
        print(f"ERROR {url}: {e}")
