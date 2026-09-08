"""
Yahoo!ファイナンス掲示板から「掲示板の声まとめ」を作る。

カテゴリ分類(クソ株・バイオ株など)はAIに判定させない。掲示板・SNSの断片的な
情報から推測するため誤判定(ハルシネーション)のリスクがあり、銘柄への評価に
関わる判定を自動化すべきではないため、ユーザーが `!tag` コマンドで手動で
付与する方式にしている(commands.py参照)。

なお、株探(kabutan)発のコメントは、AIに要約・抜粋させるのではなく
yahoo_stocks.fetch_kabutan_ranking_comments() でスクレイピングにより直接
取得している(株探の「本日のランキング【値上がり率】」記事に銘柄ごとの
短評が付いているため、そこから該当箇所をそのまま抜き出す)。

## 掲示板の遡及範囲について

前営業日15:30(前引け後の実質的な地合い区切り)以降の投稿をすべて対象に
「掲示板の声まとめ」を作る設計にしている。ただし、Yahoo!ファイナンス掲示板の
「もっと見る」(無限スクロール)は `bff-quote-stocks/v1/ajax/bbs/comment` という
セッション依存のAPIを使っており、ブラウザの実セッションなしでは
(同一パラメータで叩いても)`request parameters invalidated` で弾かれることを
確認した。ヘッドレスブラウザは使わない方針のため、この深いページングは断念し、
`/quote/XXXX.T/forum` への単純なGETで返ってくる分(出来高の多い銘柄でも
数十〜100件程度、前営業日15:30まで届かないこともある)だけを使う。
そこから前営業日15:30以降の投稿を最大200件まで抽出する。
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
HEADERS = {"User-Agent": USER_AGENT, "Accept-Language": "ja,en;q=0.9"}

DEFAULT_MODEL = "claude-haiku-4-5-20251001"
JST = ZoneInfo("Asia/Tokyo")


def previous_business_day_1530(now: datetime | None = None) -> datetime:
    """
    「前営業日15:30」を返す(土日はスキップする簡易実装。日本の祝日は
    考慮していないため、祝日明けは実際より1営業日浅くなる場合がある)。
    """
    now = now or datetime.now(JST)
    d = now.date() - timedelta(days=1)
    while d.weekday() >= 5:  # 5=土, 6=日
        d -= timedelta(days=1)
    return datetime(d.year, d.month, d.day, 15, 30, tzinfo=JST)


def get_yahoo_bbs(code: str, cutoff: datetime, max_posts: int = 200):
    """
    Yahoo!ファイナンス掲示板を取得し、cutoff以降の投稿本文を新しい順→古い順に
    並べ替えて返す(最大max_posts件)。投稿は新しい順にレンダリングされているため、
    cutoffより古い投稿に達した時点で打ち切る。
    """
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
    for art in soup.select("article"):
        text = art.get_text("\n", strip=True)
        if not text:
            continue
        m = re.search(r"(\d{4})/(\d{1,2})/(\d{1,2}) (\d{1,2}):(\d{2})", text)
        if not m:
            continue
        y, mo, d, h, mi = map(int, m.groups())
        try:
            post_dt = datetime(y, mo, d, h, mi, tzinfo=JST)
        except ValueError:
            continue
        if post_dt < cutoff:
            break  # 新しい順に並んでいるため、以降は全て対象外
        posts.append((post_dt, text[:250]))
        if len(posts) >= max_posts:
            break

    posts.sort(key=lambda p: p[0])  # 古い順(時系列)に並べ直す
    return [p[1] for p in posts]


def fallback_bbs_summary(bbs_posts):
    """Claude判定を使わない場合の簡易フォールバック。"""
    if bbs_posts:
        return f"(要約未判定) 該当期間の投稿 {len(bbs_posts)}件あり"
    return "(要約未判定) 該当期間の投稿なし"


def _build_bbs_prompt(code, name, pct, bbs_posts, cutoff):
    cutoff_label = cutoff.strftime("%m/%d %H:%M")
    posts_text = "\n".join(f"- {p}" for p in bbs_posts) or "(投稿なし)"
    return f"""以下は、本日 +{pct:.1f}% 上昇した銘柄「{name}({code})」について、
{cutoff_label}(前営業日15:30)以降にYahoo!ファイナンス掲示板へ投稿された
全{len(bbs_posts)}件です(時系列順)。

{posts_text}

上記の投稿すべてに目を通し、なぜこの銘柄が値上がりしているのかについて、
投稿者たちが挙げている理由・材料・思惑を丹念に拾い集めて、日本語で2〜4文程度に
まとめてください(「掲示板の声まとめ」)。特定の1投稿を鵜呑みにせず、複数の投稿で
共通して言及されている内容を優先してください。単なる値動きへの感想(「上がった」
「すごい」等)は理由ではないので除外してください。具体的な理由が投稿から読み取れ
ない場合は、推測せずに「具体的な理由は投稿から確認できません」のように正直に述べて
ください。見出し(「# 掲示板の声まとめ」等)・箇条書き・前置き・結びは一切付けず、
要約本文の地の文だけをそのまま出力してください。
"""


def summarize_bbs(code, name, pct, bbs_posts, cutoff, model=None):
    """
    Claude APIで「掲示板の声まとめ」を作る(カテゴリ分類はしない)。
    戻り値: (summary: str, usage: {"input_tokens", "output_tokens"})
    """
    import anthropic

    model = model or os.environ.get("ANTHROPIC_MODEL") or DEFAULT_MODEL
    client = anthropic.Anthropic()

    prompt = _build_bbs_prompt(code, name, pct, bbs_posts, cutoff)
    resp = client.messages.create(
        model=model,
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}],
    )
    usage = {
        "input_tokens": resp.usage.input_tokens,
        "output_tokens": resp.usage.output_tokens,
    }
    text_blocks = [b.text for b in resp.content if b.type == "text"]
    summary = "".join(text_blocks).strip()
    # 指示を無視して見出し行を付けてくることがあるため、念のため除去する
    summary = re.sub(r"^#+\s*.*\n+", "", summary).strip()
    return summary, usage
