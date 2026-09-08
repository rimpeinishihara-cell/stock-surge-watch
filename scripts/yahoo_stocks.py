"""
Yahoo!ファイナンスから「日本株ランキング（値上がり率）」と、個別銘柄のニュース見出しを取得する。

当初は株探(kabutan.jp)を使う予定だったが、kabutan.jpはAWS WAFのHuman Verification
(CAPTCHA)がGitHub ActionsのIPを含むデータセンター系IPを一律ブロックしており、
自動化からは一切アクセスできないことを確認した(CAPTCHAの回避は行わないため、
kabutan.jpの利用は断念した)。Yahoo!ファイナンスは同種のブロックがないことを確認済み。

ページはNext.jsアプリだが、ランキング・ニュース一覧とも静的HTMLに内容がそのまま
含まれる(SSR)ことを確認済み。CSSクラス名にはビルドごとに変わりうるハッシュ接尾辞が
付くため、クラス名の完全一致には依存せず、id・タグ構造・クラス名のプレフィックスで
要素を特定している。
"""
import re
import time

import requests
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
HEADERS = {"User-Agent": USER_AGENT, "Accept-Language": "ja,en;q=0.9"}

RANKING_URL = "https://finance.yahoo.co.jp/stocks/ranking/up"


def _class_prefix(tag, prefix):
    classes = tag.get("class") or []
    return [c for c in classes if c.startswith(prefix)]


def get_surge_list(threshold_pct: float = 10.0, max_pages: int = 25):
    """
    日本株ランキング(値上がり率・全市場)を上位ページから取得し、threshold_pct(%)以上の
    銘柄だけを返す。ランキングは降順なので、閾値を下回った時点で打ち切る。
    """
    results = []
    for page in range(1, max_pages + 1):
        params = {"market": "all"}
        if page > 1:
            params["page"] = page
        try:
            resp = requests.get(RANKING_URL, params=params, headers=HEADERS, timeout=20)
        except requests.RequestException as e:
            print(f"[yahoo_stocks] ERROR ranking page={page}: {e}")
            break
        if resp.status_code != 200:
            print(f"[yahoo_stocks] ERROR ranking page={page} status={resp.status_code}")
            break
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")
        table = soup.select_one("#item table")
        if not table:
            break
        rows = table.select("tbody tr")
        if not rows:
            break

        threshold_reached = False
        for row in rows:
            cells = row.find_all(["td", "th"])
            if len(cells) < 4:
                continue
            name_cell, price_cell, change_cell = cells[1], cells[2], cells[3]

            name_link = name_cell.find("a")
            name = name_link.get_text(strip=True) if name_link else ""
            supplements = [li.get_text(strip=True) for li in name_cell.select("li")]
            code = supplements[0] if supplements else None
            market = supplements[1] if len(supplements) > 1 else ""

            price_spans = [s for s in price_cell.find_all("span")
                           if _class_prefix(s, "StyledNumber__value")]
            price_text = price_spans[0].get_text(strip=True) if price_spans else ""

            change_spans = [s for s in change_cell.find_all("span")
                             if _class_prefix(s, "StyledNumber__value")]
            if len(change_spans) < 2:
                continue
            pct_text = change_spans[1].get_text(strip=True).replace("+", "").replace(",", "")
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
    """個別銘柄のニュース見出し(日時・タイトル)を新しい順に取得する。"""
    url = f"https://finance.yahoo.co.jp/quote/{code}.T/news"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
    except requests.RequestException as e:
        print(f"[yahoo_stocks] ERROR news code={code}: {e}")
        return []
    if resp.status_code != 200:
        return []
    resp.encoding = "utf-8"
    soup = BeautifulSoup(resp.text, "html.parser")
    items = []
    for art in soup.select("article"):
        text = art.get_text("\n", strip=True)
        if not text:
            continue
        lines = [l for l in text.split("\n") if l.strip()]
        if not lines:
            continue
        # 先頭行がタイトル、末尾付近に時刻(HH:MM)があることが多い
        title = lines[0]
        time_match = None
        for l in lines[1:4]:
            if re.match(r"^\d{1,2}:\d{2}$", l.strip()):
                time_match = l.strip()
                break
        items.append({"datetime": time_match or "", "title": title})
        if len(items) >= max_items:
            break
    return items
