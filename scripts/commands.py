"""
コマンド用チャンネル(通知先と同じチャンネル)に投稿されたメッセージを解釈して実行する。

対応コマンド:
  !help                          … コマンド一覧を表示
  !mute <証券コード>              … その銘柄を今後通知しない
  !unmute <証券コード>            … ミュート解除
  !muted                         … 現在のミュート設定(銘柄・カテゴリ)を表示

  !addcat <カテゴリ名> [説明]     … カテゴリを追加/更新(自分で好きな名前を作れる)
  !delcat <カテゴリ名>            … カテゴリを削除
  !categories                    … 現在のカテゴリ一覧(説明つき)を表示
  !mutecat <カテゴリ名>           … そのカテゴリの銘柄をまとめて非表示にする(「◯◯ N件」表示)
  !unmutecat <カテゴリ名>         … カテゴリのミュート解除

反映は次回の実行(1日1回)からになる。
"""
from __future__ import annotations

import storage

DEFAULT_CATEGORIES = {
    "決算・業績": "決算発表・業績修正・配当修正などが材料",
    "材料": "新製品・業務提携・M&A・新規契約など具体的な好材料",
    "バイオ株": "医薬品・バイオテクノロジー関連銘柄",
    "クソ株": "具体的な材料が見当たらず、出来高だけ膨らむ投機的・思惑先行の急騰",
    "市況・全体": "個別材料ではなく地合い・指数全体の動きに連動した上昇",
    "その他": "上記のどれにも当てはまらない、または理由が特定できない",
}


def ensure_default_categories():
    cats = storage.load("categories.json", None)
    if cats is None:
        storage.save("categories.json", DEFAULT_CATEGORIES)
        return dict(DEFAULT_CATEGORIES)
    return cats


def _help_text() -> str:
    cats = ensure_default_categories()
    cat_list = "\n".join(f"  ・{name} — {desc}" for name, desc in cats.items())
    return f"""**📈 株価急騰ウォッチ コマンド一覧**
(このチャンネルに打ち込んでください。反映は次回の実行(1日1回)からになります)

`!mute <証券コード>`
  その銘柄を今後一切通知しないようにします(例: `!mute 2330`)
`!unmute <証券コード>`
  ミュートを解除します
`!muted`
  現在のミュート設定(銘柄・カテゴリ)を表示します

`!addcat <カテゴリ名> [説明]`
  カテゴリを追加/更新します。説明は判定の参考に使われます
  (例: `!addcat 仕手筋 出来高が急増し値動きが荒い銘柄`)
`!delcat <カテゴリ名>`
  カテゴリを削除します
`!categories`
  現在のカテゴリ一覧を表示します
`!mutecat <カテゴリ名>`
  そのカテゴリの銘柄を詳細表示せず「カテゴリ名 N件」のようにまとめます
  (例: `!mutecat クソ株`)
`!unmutecat <カテゴリ名>`
  カテゴリのミュートを解除します

`!help`
  この一覧を表示します

**現在のカテゴリ**
{cat_list}
"""


def handle_command(content: str) -> str | None:
    content = content.strip()
    if not content.startswith("!"):
        return None

    parts = content.split(maxsplit=1)
    cmd = parts[0][1:].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd == "help":
        return _help_text()
    if cmd == "mute":
        return _cmd_mute(arg)
    if cmd == "unmute":
        return _cmd_unmute(arg)
    if cmd == "muted":
        return _cmd_muted()
    if cmd == "addcat":
        return _cmd_addcat(arg)
    if cmd == "delcat":
        return _cmd_delcat(arg)
    if cmd == "categories":
        return _cmd_categories()
    if cmd == "mutecat":
        return _cmd_mutecat(arg)
    if cmd == "unmutecat":
        return _cmd_unmutecat(arg)

    return f"❓ 知らないコマンドです: `!{cmd}`\n`!help` でコマンド一覧を見られます。"


def _cmd_mute(arg: str) -> str:
    code = arg.strip().upper()
    if not code:
        return "使い方: `!mute 2330`"
    codes = storage.load("mute_codes.json", [])
    if code in codes:
        return f"すでにミュート済みです: {code}"
    codes.append(code)
    storage.save("mute_codes.json", codes)
    return f"🔇 ミュートしました: {code}(次回実行から反映)"


def _cmd_unmute(arg: str) -> str:
    code = arg.strip().upper()
    codes = storage.load("mute_codes.json", [])
    if code not in codes:
        return f"ミュートされていません: {code}"
    codes.remove(code)
    storage.save("mute_codes.json", codes)
    return f"🔊 ミュート解除しました: {code}"


def _cmd_muted() -> str:
    codes = storage.load("mute_codes.json", [])
    cats = storage.load("mute_categories.json", [])
    lines = ["**現在のミュート設定**"]
    lines.append("銘柄: " + (", ".join(codes) if codes else "(なし)"))
    lines.append("カテゴリ: " + (", ".join(cats) if cats else "(なし)"))
    return "\n".join(lines)


def _cmd_addcat(arg: str) -> str:
    if not arg:
        return "使い方: `!addcat カテゴリ名 [説明]`\n例: `!addcat 仕手筋 出来高が急増し値動きが荒い銘柄`"
    bits = arg.split(maxsplit=1)
    name = bits[0]
    desc = bits[1] if len(bits) > 1 else "(説明なし。名前から判断)"
    cats = ensure_default_categories()
    is_update = name in cats
    cats[name] = desc
    storage.save("categories.json", cats)
    verb = "更新" if is_update else "追加"
    return f"✅ カテゴリを{verb}しました: **{name}** — {desc}"


def _cmd_delcat(arg: str) -> str:
    name = arg.strip()
    if not name:
        return "使い方: `!delcat カテゴリ名`"
    cats = ensure_default_categories()
    if name not in cats:
        return f"そのカテゴリはありません: {name}"
    del cats[name]
    storage.save("categories.json", cats)
    # ミュート設定にも入っていれば一緒に外す
    mute_cats = storage.load("mute_categories.json", [])
    if name in mute_cats:
        mute_cats.remove(name)
        storage.save("mute_categories.json", mute_cats)
    return f"🗑️ カテゴリを削除しました: {name}"


def _cmd_categories() -> str:
    cats = ensure_default_categories()
    if not cats:
        return "現在、カテゴリは登録されていません。`!addcat` で追加できます。"
    lines = ["**現在のカテゴリ一覧**"]
    for name, desc in cats.items():
        lines.append(f"・{name} — {desc}")
    return "\n".join(lines)


def _cmd_mutecat(arg: str) -> str:
    name = arg.strip()
    if not name:
        return "使い方: `!mutecat クソ株`\n`!categories` で現在のカテゴリ一覧を見られます。"
    cats = ensure_default_categories()
    if name not in cats:
        return f"そのカテゴリは登録されていません: {name}\n先に `!addcat {name} 説明` で作成してください。"
    mute_cats = storage.load("mute_categories.json", [])
    if name in mute_cats:
        return f"すでにミュート済みのカテゴリです: {name}"
    mute_cats.append(name)
    storage.save("mute_categories.json", mute_cats)
    return f"🔇 カテゴリをミュートしました: {name}(次回実行から反映)"


def _cmd_unmutecat(arg: str) -> str:
    name = arg.strip()
    mute_cats = storage.load("mute_categories.json", [])
    if name not in mute_cats:
        return f"ミュートされていないカテゴリです: {name}"
    mute_cats.remove(name)
    storage.save("mute_categories.json", mute_cats)
    return f"🔊 カテゴリのミュートを解除しました: {name}"
