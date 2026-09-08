"""
コマンド用チャンネル(通知先と同じチャンネル)に投稿されたメッセージを解釈して実行する。

対応コマンド:
  !help                          … コマンド一覧を表示
  !mute <証券コード...> [期間]    … 銘柄(複数可)を通知しない。期間指定で自動解除
  !unmute <証券コード...>         … ミュート解除(複数可)
  !muted                         … 現在のミュート設定(銘柄・カテゴリ)を表示

  !tag <証券コード...> <カテゴリ名> … 銘柄(複数可)にカテゴリを手動で付与する
  !tagmute <証券コード...> <カテゴリ名> … タグ付けとカテゴリのミュートを同時に行う
  !untag <証券コード...>          … タグを削除(複数可)
  !tags                          … 現在のタグ付け一覧を表示
  !mutecat <カテゴリ名>           … そのカテゴリの銘柄をまとめて非表示にする(「◯◯ N件」表示)
  !unmutecat <カテゴリ名>         … カテゴリのミュート解除

カテゴリ(「クソ株」等)はAIが自動判定するのではなく、ここでユーザーが手動で
付与したタグに基づく。掲示板・SNSの断片情報からの自動判定は誤判定
(ハルシネーション)のリスクがあるため採用していない。

反映は次回の実行(1日1回)からになる。
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import storage

JST = ZoneInfo("Asia/Tokyo")

_DURATION_RE = re.compile(r"^(\d+)(日|d|週間|週|w|ヶ月|ケ月|か月|カ月|m)$", re.IGNORECASE)
_DURATION_UNIT_DAYS = {
    "日": 1, "d": 1,
    "週間": 7, "週": 7, "w": 7,
    "ヶ月": 30, "ケ月": 30, "か月": 30, "カ月": 30, "m": 30,
}


def _today_jst() -> date:
    return datetime.now(JST).date()


def _parse_duration_days(token: str) -> int | None:
    """'30日' '4週' '1ヶ月' のような期間指定を日数に変換する。該当しなければNone。"""
    m = _DURATION_RE.match(token)
    if not m:
        return None
    n = int(m.group(1))
    return n * _DURATION_UNIT_DAYS[m.group(2).lower()]


def _help_text() -> str:
    tags = storage.load("tags.json", {})
    if tags:
        tag_list = "\n".join(f"  ・{code} → {cat}" for code, cat in sorted(tags.items()))
    else:
        tag_list = "  (なし)"
    return f"""**📈 株価急騰ウォッチ コマンド一覧**
(このチャンネルに打ち込んでください。反映は次回の実行(1日1回)からになります)

`!mute <証券コード...> [期間]`
  銘柄を通知しないようにします。複数銘柄をまとめて指定できます
  (例: `!mute 2330` (無期限) / `!mute 2330 6203 8995 30日` (期間限定・複数可))
  期間の指定例: `30日` `4週` `1ヶ月`。省略すると無期限で、期限が来ると自動解除されます
`!unmute <証券コード...>`
  ミュートを解除します(複数可、例: `!unmute 2330 6203`)
`!muted`
  現在のミュート設定(銘柄・カテゴリ)を表示します

`!tag <証券コード...> <カテゴリ名>`
  銘柄にカテゴリを手動で付与します。複数銘柄をまとめて指定できます
  (例: `!tag 8995 クソ株` / `!tag 8995 6203 4594 クソ株`)
  末尾の1語がカテゴリ名、それ以外は全て証券コードとして扱われます
  カテゴリはAIが自動判定するのではなく、ここで付けたタグのみが使われます
`!tagmute <証券コード...> <カテゴリ名>`
  タグ付けと同時にそのカテゴリをミュートします(`!tag` + `!mutecat` を1回で)
  (例: `!tagmute 8995 6203 4594 クソ株`)
`!untag <証券コード...>`
  タグを削除します(複数可、例: `!untag 8995 6203`)
`!tags`
  現在のタグ付け一覧を表示します
`!mutecat <カテゴリ名>`
  そのカテゴリが付いた銘柄を詳細表示せず「カテゴリ名 N件」のようにまとめます
  (例: `!mutecat クソ株`。タグが付いていない銘柄はまとめられず常に詳細表示されます)
`!unmutecat <カテゴリ名>`
  カテゴリのミュートを解除します

`!help`
  この一覧を表示します

**現在のタグ付け**
{tag_list}
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
    if cmd == "tag":
        return _cmd_tag(arg)
    if cmd == "tagmute":
        return _cmd_tagmute(arg)
    if cmd == "untag":
        return _cmd_untag(arg)
    if cmd == "tags":
        return _cmd_tags()
    if cmd == "mutecat":
        return _cmd_mutecat(arg)
    if cmd == "unmutecat":
        return _cmd_unmutecat(arg)

    return f"❓ 知らないコマンドです: `!{cmd}`\n`!help` でコマンド一覧を見られます。"


def _load_mutes() -> dict:
    """mute_codes.jsonを {コード: 期限(ISO日付) or None(無期限)} の辞書で返す。
    旧形式(単純なコードのリスト)だった場合は無期限として辞書に変換する。"""
    mutes = storage.load("mute_codes.json", {})
    if isinstance(mutes, list):
        mutes = {c: None for c in mutes}
    return mutes


def load_active_mute_codes() -> set[str]:
    """
    期限切れを間引いた、現在有効なミュート銘柄コードの集合を返す(main.pyから使用)。
    旧リスト形式からの移行・期限切れの削除があった場合は正規化して保存し直す。
    """
    mutes = _load_mutes()
    original = storage.load("mute_codes.json", {})
    needs_save = isinstance(original, list)

    today = _today_jst()
    active = {}
    for code, expiry in mutes.items():
        if expiry:
            try:
                if date.fromisoformat(expiry) < today:
                    needs_save = True
                    continue
            except ValueError:
                pass
        active[code] = expiry

    if needs_save:
        storage.save("mute_codes.json", active)
    return set(active.keys())


def _cmd_mute(arg: str) -> str:
    bits = arg.split()
    if not bits:
        return (
            "使い方: `!mute 証券コード [証券コード...] [期間]`\n"
            "例: `!mute 2330` (無期限) / `!mute 2330 6203 8995 30日` (期間限定・複数可)\n"
            "期間の指定例: `30日` `4週` `1ヶ月`(未指定なら無期限)"
        )
    days = _parse_duration_days(bits[-1])
    codes = bits[:-1] if days is not None else bits
    codes = [c.upper() for c in codes]
    if not codes:
        return "使い方: `!mute 証券コード [証券コード...] [期間]`"

    expiry = (_today_jst() + timedelta(days=days)).isoformat() if days is not None else None

    mutes = _load_mutes()
    added, updated = [], []
    for code in codes:
        (updated if code in mutes else added).append(code)
        mutes[code] = expiry
    storage.save("mute_codes.json", mutes)

    period_note = f"{expiry}まで" if expiry else "無期限"
    lines = [f"🔇 ミュートしました({period_note}、次回実行から反映)"]
    if added:
        lines.append("新規: " + ", ".join(added))
    if updated:
        lines.append("更新: " + ", ".join(updated))
    return "\n".join(lines)


def _cmd_unmute(arg: str) -> str:
    codes = [c.upper() for c in arg.split()]
    if not codes:
        return "使い方: `!unmute 2330` / `!unmute 2330 6203`"
    mutes = _load_mutes()
    removed, missing = [], []
    for code in codes:
        if code in mutes:
            del mutes[code]
            removed.append(code)
        else:
            missing.append(code)
    storage.save("mute_codes.json", mutes)

    lines = []
    if removed:
        lines.append("🔊 ミュート解除しました: " + ", ".join(removed))
    if missing:
        lines.append("ミュートされていません: " + ", ".join(missing))
    return "\n".join(lines)


def _cmd_muted() -> str:
    mutes = _load_mutes()
    cats = storage.load("mute_categories.json", [])
    lines = ["**現在のミュート設定**"]
    if mutes:
        code_bits = [f"{c}({e}まで)" if e else c for c, e in sorted(mutes.items())]
        lines.append("銘柄: " + ", ".join(code_bits))
    else:
        lines.append("銘柄: (なし)")
    lines.append("カテゴリ: " + (", ".join(cats) if cats else "(なし)"))
    return "\n".join(lines)


def _parse_tag_args(arg: str, usage: str):
    """`!tag`/`!tagmute` 共通の引数パース。末尾の1語がカテゴリ名、残りが証券コード。"""
    bits = arg.split()
    if len(bits) < 2:
        return None, None, usage
    *codes, cat = bits
    return [c.upper() for c in codes], cat, None


def _apply_tags(codes, cat) -> tuple[list, list]:
    """codesすべてにcatタグを付与し、(新規に付けたコード, 更新したコード) を返す。"""
    tags = storage.load("tags.json", {})
    added, updated = [], []
    for code in codes:
        (updated if code in tags else added).append(code)
        tags[code] = cat
    storage.save("tags.json", tags)
    return added, updated


def _cmd_tag(arg: str) -> str:
    codes, cat, usage = _parse_tag_args(
        arg,
        "使い方: `!tag 証券コード [証券コード...] カテゴリ名`\n"
        "例: `!tag 8995 クソ株` / `!tag 8995 6203 4594 クソ株`\n"
        "(末尾の1語がカテゴリ名、それ以外は全て証券コードとして扱われます)",
    )
    if usage:
        return usage

    added, updated = _apply_tags(codes, cat)
    lines = [f"✅ タグを設定しました: **{cat}**"]
    if added:
        lines.append("新規: " + ", ".join(added))
    if updated:
        lines.append("更新: " + ", ".join(updated))
    return "\n".join(lines)


def _cmd_tagmute(arg: str) -> str:
    codes, cat, usage = _parse_tag_args(
        arg,
        "使い方: `!tagmute 証券コード [証券コード...] カテゴリ名`\n"
        "例: `!tagmute 8995 6203 4594 クソ株`\n"
        "(タグ付けと同時にそのカテゴリをミュートします)",
    )
    if usage:
        return usage

    added, updated = _apply_tags(codes, cat)

    mute_cats = storage.load("mute_categories.json", [])
    already_muted = cat in mute_cats
    if not already_muted:
        mute_cats.append(cat)
        storage.save("mute_categories.json", mute_cats)

    mute_note = "(既にミュート済みのカテゴリでした)" if already_muted else "(次回実行からミュート反映)"
    lines = [f"✅ タグを設定し、カテゴリ **{cat}** をミュートしました {mute_note}"]
    if added:
        lines.append("新規: " + ", ".join(added))
    if updated:
        lines.append("更新: " + ", ".join(updated))
    return "\n".join(lines)


def _cmd_untag(arg: str) -> str:
    codes = [c.upper() for c in arg.split()]
    if not codes:
        return "使い方: `!untag 8995` / `!untag 8995 6203`"
    tags = storage.load("tags.json", {})
    removed, missing = [], []
    for code in codes:
        if code in tags:
            del tags[code]
            removed.append(code)
        else:
            missing.append(code)
    storage.save("tags.json", tags)

    lines = []
    if removed:
        lines.append("🗑️ タグを削除しました: " + ", ".join(removed))
    if missing:
        lines.append("タグが付いていません: " + ", ".join(missing))
    return "\n".join(lines)


def _cmd_tags() -> str:
    tags = storage.load("tags.json", {})
    if not tags:
        return "現在、タグ付けされている銘柄はありません。`!tag` で登録できます。"
    lines = ["**現在のタグ付け一覧**"]
    for code, cat in sorted(tags.items()):
        lines.append(f"・{code} → {cat}")
    return "\n".join(lines)


def _cmd_mutecat(arg: str) -> str:
    name = arg.strip()
    if not name:
        return "使い方: `!mutecat クソ株`\n`!tags` で現在のタグ付け一覧を見られます。"
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
