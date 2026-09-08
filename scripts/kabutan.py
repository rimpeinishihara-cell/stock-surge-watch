"""
株探(kabutan.jp)から「今日の株価上昇率ランキング」と、個別銘柄のニュース見出しを取得する。

ランキングページは単純な GET (ブラウザらしき User-Agent 付き) で 200 が返り、
テーブルもJSなしの静的HTMLに含まれることを確認済み。
"""
import time

import requests
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
HEADERS = {"User-Agent": USER_AGENT, "Accept-Language": "ja,en;q=0.9"}

RANKING_URL = "https://kabutan.jp/warning/"
NEWS_URL = "https://kabutan.jp/stock/news"


def get_surge_list(threshold_pct: float = 10.0, max_pages: int = 20):
    """
    値上がり率ランキング(全市場)を上位ページから取得し、threshold_pct(%)以上の
    銘柄だけを返す。ランキングは降順なので、閾値を下回った時点で打ち切る。
    """
    results = []
    for page in range(1, max_pages + 1):
        params = {
            "mode": "2_1",
            "market": "0",
            "capitalization": "-1",
            "dispmode": "normal",
            "stc": "",
            "stm": "0",
            "pagecount": "50",
            "page": page,
        }
        try:
            resp = requests.get(RANKING_URL, params=params, headers=HEADERS, timeout=20)
        except requests.RequestException as e:
            print(f"[kabutan] ERROR page={page}: {e}")
            break
        if resp.status_code != 200:
            print(f"[kabutan] ERROR page={page} status={resp.status_code} headers={dict(resp.headers)} body={resp.text[:500]!r}")
            break
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")
        table = soup.select_one("table.stock_table.st_market")
        if not table:
            break
        rows = table.select("tbody tr")
        if not rows:
            break

        threshold_reached = False
        for row in rows:
            cells = row.find_all(["td", "th"])
            if len(cells) < 9:
                continue
            code_link = cells[0].find("a")
            code = code_link.get_text(strip=True) if code_link else None
            name = cells[1].get_text(strip=True)
            market = cells[2].get_text(strip=True)
            price_text = cells[5].get_text(strip=True)
            pct_cell = cells[8]
            pct_span = pct_cell.find("span")
            pct_text = (pct_span.get_text(strip=True) if pct_span else pct_cell.get_text(strip=True))
            pct_text = pct_text.replace("%", "").replace(",", "").replace("+", "")
            try:
                pct = float(pct_text)
            except ValueError:
                continue
            if pct < threshold_pct:
                threshold_reached = True
                break
            if not code:
                continue
            results.append({
                "code": code,
                "name": name,
                "market": market,
                "price": price_text,
                "change_pct": pct,
            })
        if threshold_reached:
            break
        time.sleep(0.3)
    return results


def get_stock_news(code: str, max_items: int = 6):
    """個別銘柄のニュース見出し(日時・カテゴリ・タイトル)を新しい順に取得する。"""
    try:
        resp = requests.get(NEWS_URL, params={"code": code}, headers=HEADERS, timeout=20)
    except requests.RequestException as e:
        print(f"[kabutan] ERROR news code={code}: {e}")
        return []
    if resp.status_code != 200:
        return []
    resp.encoding = "utf-8"
    soup = BeautifulSoup(resp.text, "html.parser")
    table = soup.select_one("table.s_news_list")
    if not table:
        return []
    items = []
    for row in table.select("tr"):
        time_tag = row.select_one("time")
        link = row.select_one("td a")
        if not time_tag or not link:
            continue
        items.append({
            "datetime": time_tag.get("datetime", ""),
            "title": link.get_text(strip=True),
        })
        if len(items) >= max_items:
            break
    return items
