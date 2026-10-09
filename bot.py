"""ECP Slack Bot v2.

- Keeps the original test reply.
- Watches #原 for start/end reports.
- Remembers the latest start report per user.
- Falls back to Slack history after a Railway restart.
- Reuses formatter_core.js through PythonMonkey so formatter behavior stays aligned
  with the existing web formatter.
- Posts the completed sheet to #原データ作成.
"""

import logging
import os
import re
from threading import Lock

import pythonmonkey as pm
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ecp-bot")


def required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing environment variable: {name}")
    return value


SLACK_BOT_TOKEN = required_env("SLACK_BOT_TOKEN")
SLACK_APP_TOKEN = required_env("SLACK_APP_TOKEN")

# Can be overridden later from Railway Variables.
SOURCE_CHANNEL_ID = os.environ.get("ECP_SOURCE_CHANNEL_ID", "C0C7WHETTC6").strip()  # #原
OUTPUT_CHANNEL_ID = os.environ.get("ECP_OUTPUT_CHANNEL_ID", "C0C8STM7WDN").strip()  # #原データ作成
TEST_CHANNEL_ID = os.environ.get("ECP_TEST_CHANNEL_ID", "C0C7SSG07L5").strip()

MACHINE_ALIASES = [
    ("karakuri2", ["からサー2", "からサー", "からくりサーカス2", "からくり"]),
    ("monkey", ["モンキーターン", "モンキー"]),
    ("tensei", ["転生"]),
    ("ghoul", ["東京喰種", "喰種"]),
    ("tekken", ["鉄拳"]),
    ("god", ["ミリオンゴッド", "ゴッド", "GOD"]),
    ("enen", ["炎炎"]),
    ("madomagi", ["まどマギ", "マギレコ"]),
    ("tokyorev", ["東京リベンジャーズ", "東リべ", "東リベ"]),
    ("sao", ["SAO"]),
    ("rikoriko", ["リコリスリコイル", "リコリス・リコイル", "リコリス", "リコリコ"]),
    ("hokuto", ["北斗の拳", "北斗"]),
]

# Reuse the exact formatter logic already committed in formatter_core.js.
# PythonMonkey's require() is relative to this Python file and expects no extension.
formatter = pm.require("./formatter_core")
parse_memo_text = formatter["parseMemoText"]
generate_output = formatter["generateOutput"]
check_missing = formatter["checkMissing"]
get_store_rate = formatter["getStoreRate"]
machine_info = formatter["MACHINE_INFO"]

pending_starts = {}
processed_events = set()
state_lock = Lock()


def normalized_lines(text: str):
    return [line.strip() for line in str(text or "").replace("\r", "").split("\n") if line.strip()]


def is_start_report(text: str) -> bool:
    lines = normalized_lines(text)
    has_machine_number = any(re.match(r"^台番\s*\d+", line) for line in lines)
    has_start = any(re.match(r"^始め(?:[（(]データランプ[）)])?\s*-?\d+", line) for line in lines)
    has_end_marker = any(re.match(r"^抜け\s*[\d#]+", line) for line in lines)
    return has_machine_number and has_start and not has_end_marker


def is_end_report(text: str) -> bool:
    lines = normalized_lines(text)
    has_out = any(re.match(r"^抜け\s*[\d#]+", line) for line in lines)
    has_get = any(re.match(r"^獲得(?:\s*[\d#]+)?$", line) for line in lines)
    has_store_or_name = any(re.match(r"^(?:店名|名前)\s*.+", line) for line in lines)
    return has_out and has_get and has_store_or_name


def detect_machine(text: str):
    lines = normalized_lines(text)
    first_line = lines[0] if lines else ""
    for key, aliases in MACHINE_ALIASES:
        for alias in aliases:
            if first_line == alias or first_line.startswith(alias + " ") or first_line.startswith(alias + "　"):
                return key
    return None


def _has_machine(machine: str) -> bool:
    try:
        return bool(machine_info[machine])
    except Exception:
        return False


def js_get(obj, key, default=""):
    try:
        value = obj[key]
    except Exception:
        return default
    return default if value is None else value


def build_output(start_text: str, end_text: str):
    machine = detect_machine(start_text)
    if not machine or not _has_machine(machine):
        return None, "開始報告の機種名を判定できませんでした。"

    memo = f"{start_text.strip()}\n{end_text.strip()}"

    try:
        parsed = parse_memo_text(memo, machine)
        missing = list(check_missing(parsed, machine))
    except Exception as exc:
        logger.exception("formatter parse/check failed")
        return None, f"フォーマッター解析でエラーが発生しました: {exc}"

    if missing:
        return None, "抜けシート生成に必要な項目が不足しています：" + "、".join(str(x) for x in missing)

    shop = js_get(parsed, "shop", "")

    try:
        rate = get_store_rate(shop or "")
    except Exception:
        logger.exception("store rate lookup failed")
        rate = None

    if not rate:
        return None, f"店舗「{shop or '未入力'}」の貸玉・換金率がフォーマッターに登録されていません。"

    chodama = js_get(parsed, "chodama", "")
    prev_val = "" if str(chodama) == "0" else str(chodama or "")

    fields = {
        "shop": str(shop or ""),
        "name": str(js_get(parsed, "name", "") or ""),
        "kinsen1": "1000",
        "kinsen2": str(rate[1]),
        "kashi1": "1000",
        "kashi2": str(rate[0]),
        "prevVal": prev_val,
        "manualKitai": "",
    }

    try:
        output = generate_output(machine, parsed, fields)
    except Exception as exc:
        logger.exception("formatter output failed")
        return None, f"抜けシート生成でエラーが発生しました: {exc}"

    if not output:
        return None, "抜けシートを生成できませんでした。"

    return str(output), None


def find_latest_start_from_history(client, user_id: str, before_ts: str):
    cursor = None
    for _ in range(3):  # up to 300 messages
        kwargs = {
            "channel": SOURCE_CHANNEL_ID,
            "latest": before_ts,
            "inclusive": False,
            "limit": 100,
        }
        if cursor:
            kwargs["cursor"] = cursor

        response = client.conversations_history(**kwargs)
        for message in response.get("messages", []):
            if message.get("user") != user_id:
                continue
            if message.get("bot_id") or message.get("subtype"):
                continue
            text = message.get("text", "")
            if is_start_report(text):
                return {"text": text, "ts": message.get("ts", "")}

        cursor = (response.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            break

    return None


def remember_event(ts: str) -> bool:
    """Return False when Slack redelivers an event already handled in this process."""
    with state_lock:
        if ts in processed_events:
            return False
        processed_events.add(ts)
        if len(processed_events) > 500:
            processed_events.pop()
    return True


app = App(token=SLACK_BOT_TOKEN)


@app.event("message")
def on_message(event, say, client):
    if event.get("subtype") or event.get("bot_id"):
        return

    user_id = event.get("user")
    ts = event.get("ts")
    channel_id = event.get("channel")
    text = str(event.get("text", "") or "").strip()

    if not user_id or not ts:
        return
    if not remember_event(ts):
        return

    # Keep the original connectivity test alive.
    if channel_id == TEST_CHANNEL_ID and text == "テスト":
        say(text="ECP-Bot、正常に稼働中です！🤖", thread_ts=ts)
        return

    if channel_id != SOURCE_CHANNEL_ID:
        return

    if is_start_report(text):
        with state_lock:
            pending_starts[user_id] = {"text": text, "ts": ts}
        logger.info("ECP start stored user=%s machine=%s", user_id, detect_machine(text) or "unknown")
        return

    # Intermediate notes such as "159GG" / "現金再開6000〜215" are ignored.
    if not is_end_report(text):
        return

    with state_lock:
        start = pending_starts.get(user_id)

    if not start or float(start.get("ts", 0) or 0) >= float(ts):
        start = find_latest_start_from_history(client, user_id, ts)

    if not start:
        say(
            text="⚠️ ECP-Bot: この抜け報告より前の開始報告が見つかりませんでした。",
            thread_ts=ts,
        )
        return

    output, error = build_output(start["text"], text)
    if error:
        say(text=f"⚠️ ECP-Bot: {error}", thread_ts=ts)
        return

    client.chat_postMessage(channel=OUTPUT_CHANNEL_ID, text=output)

    with state_lock:
        pending_starts.pop(user_id, None)

    logger.info("ECP sheet posted user=%s machine=%s end_ts=%s", user_id, detect_machine(start["text"]), ts)


def main():
    logger.info("Starting ECP-Bot v2")
    logger.info("#原: %s -> #原データ作成: %s", SOURCE_CHANNEL_ID, OUTPUT_CHANNEL_ID)
    SocketModeHandler(app, SLACK_APP_TOKEN).start()


if __name__ == "__main__":
    main()
