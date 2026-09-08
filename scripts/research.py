"""
Yahoo!ファイナンス掲示板・X(Yahoo!リアルタイム検索経由)から材料を集め、
Claude APIで急騰理由の要約を行う。ニュース見出しは yahoo_stocks.py
(Yahoo!ファイナンスのニュースタブ)から渡される。

カテゴリ分類(クソ株・バイオ株など)はAIに判定させない。掲示板・SNSの断片的な
情報から推測するため誤判定(ハルシネーション)のリスクがあり、銘柄への評価に
関わる判定を自動化すべきではないため、ユーザーが `!tag` コマンドで手動で
付与する方式にしている(commands.py参照)。要約自体にも誤りが含まれうるため、
投稿には毎回「AIによる自動要約」である旨の注記を付けている(main.py参照)。

- Yahoo!ファイナンス掲示板(/forum)は静的HTMLに投稿本文が含まれることを確認済み。
- Xは公式APIが従量課金制のため、wom-buzz-watch と同じく
  Yahoo!リアルタイム検索(X投稿の非公式ミラー)を代替として使う。
"""
import json
import os
import re
import urllib.parse

import requests
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
HEADERS = {"User-Agent": USER_AGENT, "Accept-Language": "ja,en;q=0.9"}

DEFAULT_MODEL = "claude-haiku-4-5-20251001"


def get_yahoo_bbs(code: str, max_posts: int = 5):
    """Yahoo!ファイナンス掲示板の直近の投稿本文を取得する。"""
    url = f"https://finance.yahoo.co.jp/quote/{code}.T/forum"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
    except requests.RequestException as e:
        print(f"[research] ERROR yahoo bbs code={code}: {e}")
        return []
    if resp.status_code != 200:
        return []
    resp.encoding = "utf-8"
    soup = BeautifulSoup(resp.text, "html.parser")
    posts = []
    for art in soup.select("article")[:max_posts]:
        text = art.get_text("\n", strip=True)
        if text:
            posts.append(text[:400])
    return posts


def get_x_buzz(keyword: str, max_posts: int = 5):
    """Yahoo!リアルタイム検索経由でX(旧Twitter)の関連投稿を取得する。"""
    url = f"https://search.yahoo.co.jp/realtime/search/{urllib.parse.quote(keyword)}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
    except requests.RequestException as e:
        print(f"[research] ERROR x buzz keyword={keyword}: {e}")
        return []
    if resp.status_code != 200:
        return []
    resp.encoding = "utf-8"
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', resp.text, re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
        entries = data["props"]["pageProps"]["pageData"].get("timeline", {}).get("entry", [])
    except Exception:
        return []
    posts = []
    for e in entries[:max_posts]:
        text = (e.get("displayText") or "").strip()
        if text:
            posts.append(text[:300])
    return posts


def fallback_reason(news):
    """Claude判定を使わない場合の簡易フォールバック。"""
    if news:
        return f"(材料未判定) 関連ニュース: {news[0]['title']}"
    return "(材料未判定) 関連ニュースなし"


def _build_prompt(code, name, pct, news, bbs_posts, x_posts):
    news_text = "\n".join(f"- ({n['datetime'][:10]}) {n['title']}" for n in news) or "(なし)"
    bbs_text = "\n".join(f"- {p}" for p in bbs_posts) or "(なし)"
    x_text = "\n".join(f"- {p}" for p in x_posts) or "(なし)"
    return f"""以下は本日 +{pct:.1f}% 上昇した銘柄「{name}({code})」に関する情報です。

# Yahoo!ファイナンスニュース(この銘柄の最近のニュース見出し)
{news_text}

# Yahoo!ファイナンス掲示板の直近の投稿
{bbs_text}

# X(旧Twitter)の関連投稿(Yahoo!リアルタイム検索経由)
{x_text}

上記の情報から、この銘柄が本日値上がりした理由を1〜2文で日本語要約してください。
複数銘柄をまとめた市況記事や無関係な投稿を、この銘柄固有の理由と誤って結び付けない
でください。情報が乏しく理由が特定できない場合は、推測せずにその旨を明記してください。
要約以外の文章(前置き・結び等)は不要です。
"""


def summarize_reason(code, name, pct, news, bbs_posts, x_posts, model=None):
    """
    Claude APIで急騰理由の要約のみを行う(カテゴリ分類はしない)。
    戻り値: (reason: str, usage: {"input_tokens", "output_tokens"})
    """
    import anthropic

    model = model or os.environ.get("ANTHROPIC_MODEL") or DEFAULT_MODEL
    client = anthropic.Anthropic()

    prompt = _build_prompt(code, name, pct, news, bbs_posts, x_posts)
    resp = client.messages.create(
        model=model,
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}],
    )
    usage = {
        "input_tokens": resp.usage.input_tokens,
        "output_tokens": resp.usage.output_tokens,
    }
    text_blocks = [b.text for b in resp.content if b.type == "text"]
    reason = "".join(text_blocks).strip()
    return reason, usage
