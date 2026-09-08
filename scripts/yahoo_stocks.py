"""
Yahoo!ファイナンスから「日本株ランキング（値上がり率）」、個別銘柄のニュース見出し、
そして株探(kabutan)発の「本日のランキング【値上がり率】」記事(銘柄ごとの短評付き)を取得する。

当初は株探(kabutan.jp)に直接アクセスする予定だったが、kabutan.jpはAWS WAFの
Human Verification(CAPTCHA)がGitHub ActionsのIPを含むデータセンター系IPを
一律ブロックしており、自動化からは一切アクセスできないことを確認した(CAPTCHAの
回避は行わないため、kabutan.jpへの直接アクセスは断念した)。
その代わり、Yahoo!ファイナンスの個別銘柄ニュース(/quote/XXXX.T/news)には
「株探ニュース」を出典とする記事がそのまま配信されており、その中の
「本日のランキング【値上がり率】」という記事には、値上がり率上位銘柄それぞれに
対する株探の短いコメント(個別ニュース／決算速報／テーマ)が付いていることを確認した。
これをそのまま抜粋して使うことで、AIの推測を挟まずに株探由来のコメントを提示できる。

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
BASE_URL = "https://finance.yahoo.co.jp"
KABUTAN_RANKING_TITLE_PREFIX = "本日のランキング【値上がり率】"


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
            name = name.replace("(株)", "").strip()
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


def get_stock_news(code: str, max_items: int = 10):
    """個別銘柄のニュース見出し(日時・タイトル・出典・詳細URL)を新しい順に取得する。"""
    url = f"{BASE_URL}/quote/{code}.T/news"
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
        link = art.find("a")
        title_el = art.find("h3")
        if not link or not title_el:
            continue
        title = title_el.get_text(strip=True)
        time_el = art.find("time")
        datetime_text = time_el.get_text(strip=True) if time_el else ""
        media_lis = [li for li in art.select("li") if _class_prefix(li, "_NewsItem__supplement--media")]
        media = media_lis[0].get_text(strip=True) if media_lis else ""
        href = link.get("href") or ""
        items.append({
            "datetime": datetime_text,
            "title": title,
            "media": media,
            "url": (BASE_URL + href) if href.startswith("/") else href,
        })
        if len(items) >= max_items:
            break
    return items


def _parse_kabutan_ranking_article(text: str) -> dict:
    """
    株探の「本日のランキング【値上がり率】」記事本文(get_textしたテキスト)から、
    銘柄コードごとの個別コメントを抽出する。コメントが無い銘柄は空文字列。
    「よく比較される銘柄」など、ランキング表以降のセクションは対象外にする。
    """
    cutoff = text.find("よく比較される銘柄")
    if cutoff != -1:
        text = text[:cutoff]

    lines = text.split("\n")
    comments = {}
    i = 0
    while i < len(lines):
        if lines[i].strip() == "<" and i + 2 < len(lines) and lines[i + 2].strip() == ">":
            code = lines[i + 1].strip()
            info_line = lines[i + 3] if i + 3 < len(lines) else ""
            m = re.match(
                r"^\S+[\s　]+\S+[\s　]+[\d.]+[\s　]+\d+[\s　]*(?:S[\s　]*)?(.*)$",
                info_line.strip(),
            )
            comments[code] = m.group(1).strip() if m else ""
            i += 4
        else:
            i += 1
    return comments


def fetch_kabutan_ranking_comments(sample_codes) -> dict:
    """
    今日の値上がり銘柄のうち何件かのニュース一覧から、株探の
    「本日のランキング【値上がり率】」記事(全銘柄で共通)を見つけて取得し、
    {証券コード: コメント} の辞書を返す。見つからなければ空dict。
    """
    for code in sample_codes:
        news = get_stock_news(code, max_items=15)
        article = next(
            (n for n in news if n["media"] == "株探ニュース"
             and n["title"].startswith(KABUTAN_RANKING_TITLE_PREFIX)),
            None,
        )
        if not article:
            continue
        try:
            resp = requests.get(article["url"], headers=HEADERS, timeout=20)
        except requests.RequestException as e:
            print(f"[yahoo_stocks] ERROR kabutan ranking article: {e}")
            continue
        if resp.status_code != 200:
            continue
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")
        art = soup.find("article")
        if not art:
            continue
        return _parse_kabutan_ranking_article(art.get_text("\n", strip=True))
    return {}
