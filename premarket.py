"""夜盤盤前快報

早上排程（台灣 08:00，夜盤 05:00 收完、開盤 09:00 前）讀昨晚台指期夜盤
+ 昨日選股訊號，推播今日開盤方向預判與個股順風/逆風對照。

執行: uv run python premarket.py
"""

import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo


try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from stock_strategies.night_session import get_night_session
from stock_strategies.sheet import read_latest_signals
from stock_strategies.notify import send_telegram, format_premarket


REQUIRED_ENV = [
    "FINMIND_TOKEN",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "GOOGLE_SHEET_ID",
    "GOOGLE_CREDS_JSON",
]


def main():
    # Check if today is Saturday (Taiwan time) and skip sending
    taiwan_tz = ZoneInfo("Asia/Taipei")
    now_tw = datetime.now(taiwan_tz)
    if now_tw.weekday() == 5:  # Saturday
        print("⚠️ Saturday run skipped; no Telegram notification.")
        sys.exit(0)

    missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
    if missing:
        print(f"❌ 缺少環境變數: {missing}", file=sys.stderr)
        sys.exit(1)

    # 1. 取得台指期夜盤
    now_tw = datetime.now(taiwan_tz)
    print(f"[{now_tw}] 取得台指期夜盤...")
    night = get_night_session()
    if night:
        print(f"  → {night['date']} 夜盤 {night['pct']:+.2f}% ({night['label']})")
    else:
        print("  → 夜盤資料暫時取不到")

    # 2. 讀昨日訊號（沿用 14:30 排程寫進 Signals 分頁的結果）
    #    讀 300 筆以涵蓋多日（單日掃描可能就數十筆 SKIP），
    #    format_premarket 會自動挑「最近一批有 BUY/WATCH」的日期。
    print("讀取昨日訊號...")
    signals = []
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            signals = read_latest_signals(limit=300)
            break
        except Exception as e:
            print(f"⚠️ 讀取訊號失敗 (嘗試 {attempt}/{max_retries}): {e}", file=sys.stderr)
            if attempt < max_retries:
                import time
                time.sleep(2)
    print(f"  → {len(signals)} 筆")

    # 3. 發送 Telegram 盤前快報
    print("發送 Telegram 盤前快報...")
    send_telegram(format_premarket(night, signals))
    print("✅ 完成")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        err_msg = f"❌ Premarket script error: {e}"
        print(err_msg, file=sys.stderr)
        try:
            send_telegram(err_msg)
        except Exception:
            pass
        raise
