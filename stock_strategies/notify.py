import logging
import os
import sys
from datetime import datetime
from typing import Any, Optional

import numpy as np
import requests

from .config import CONFIG, TELEGRAM_API

logger = logging.getLogger(__name__)


def send_telegram(text: str) -> None:
    """發送 Telegram Markdown 格式訊息。"""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        logger.warning("未設定 TELEGRAM_BOT_TOKEN 或 TELEGRAM_CHAT_ID，略過推播。")
        return

    url = TELEGRAM_API.format(token=token)
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        if not r.ok:
            logger.error("Telegram 發送失敗: %s", r.text)
            print(f"Telegram 送失敗: {r.text}", file=sys.stderr)
    except requests.RequestException as e:
        logger.error("Telegram 網路連線錯誤: %s", e)
        print(f"Telegram 連線錯誤: {e}", file=sys.stderr)


def _trend_emoji(chg: float) -> str:
    if chg > 3:
        return "🔥"
    elif chg > 0:
        return "📈"
    elif chg > -3:
        return "📉"
    return "💥"


def _deduplicate_signals(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同檔股票若被多策略選中，合併策略標籤並以最優訊號與最高分為基準，杜絕重複洗版。"""
    strat_labels = {
        "chan_longterm": "纏論一買",
        "chan_shortterm": "纏論三買",
        "conservative": "保守型",
        "default": "預設V3.2",
    }
    merged: dict[str, dict[str, Any]] = {}

    for s in signals:
        sid = str(s.get("stock_id", ""))
        if not sid:
            continue

        raw_strat = s.get("strategy_id", "default")
        strat_name = strat_labels.get(raw_strat, raw_strat)

        if sid not in merged:
            s_copy = dict(s)
            s_copy["matched_strats"] = [strat_name]
            merged[sid] = s_copy
        else:
            if strat_name not in merged[sid]["matched_strats"]:
                merged[sid]["matched_strats"].append(strat_name)

            # 若有任一策略判定為 BUY，優先晉級為 BUY
            if s.get("action") == "BUY" and merged[sid].get("action") != "BUY":
                merged[sid]["action"] = "BUY"
                merged[sid]["entry_price"] = s.get("entry_price")
                merged[sid]["stop_loss_price"] = s.get("stop_loss_price")
                merged[sid]["target_price"] = s.get("target_price")
                merged[sid]["risk_reward_ratio"] = s.get("risk_reward_ratio")
                merged[sid]["signal_score"] = s.get("signal_score", 0)
                merged[sid]["components"] = s.get("components", {})
            elif s.get("signal_score", 0) > merged[sid].get("signal_score", 0):
                # 同等級下保留較高分者的價格與技術評分
                merged[sid]["signal_score"] = s.get("signal_score", 0)
                if s.get("entry_price"):
                    merged[sid]["entry_price"] = s.get("entry_price")
                if s.get("stop_loss_price"):
                    merged[sid]["stop_loss_price"] = s.get("stop_loss_price")
                if s.get("target_price"):
                    merged[sid]["target_price"] = s.get("target_price")
                if s.get("risk_reward_ratio"):
                    merged[sid]["risk_reward_ratio"] = s.get("risk_reward_ratio")
                if s.get("components"):
                    merged[sid]["components"] = s.get("components")

    return list(merged.values())


def _explain_why(s: dict[str, Any]) -> str:
    """簡潔解釋為什麼是 WATCH 或未達標"""
    c = s.get("components", {})
    reasons = []
    if not c.get("fundamental_pass"):
        reasons.append("基本面未達標")
    if c.get("tech_score", 0) < 50:
        reasons.append(f"技術分僅{c.get('tech_score', 0)}")
    if s.get("signal_score", 0) < CONFIG.get("min_total_score_for_buy", 65):
        reasons.append(f"總分{s.get('signal_score', 0)}未達65")
    if not reasons:
        return "綜合評分達標"
    return " / ".join(reasons)


def _market_sentiment(signals: list[dict[str, Any]]) -> str:
    """評估市場池內整體氣氛"""
    valid = [s for s in signals if s.get("trend")]
    if not valid:
        return "中性觀望"
    up = sum(1 for s in valid if s["trend"].get("chg_5d", 0) > 0)
    above_ma20 = sum(1 for s in valid if s["trend"].get("above_ma20"))
    pct_up = up / len(valid) * 100
    pct_ma20 = above_ma20 / len(valid) * 100

    if pct_up > 70 and pct_ma20 > 60:
        return "🟢 偏多（多數標的強勢且站穩月線）"
    elif pct_up > 50:
        return "🟡 中性偏多（個股表現分歧，選股不選市）"
    elif pct_up > 30:
        return "🟠 中性偏空（多數標的修正，嚴控部位）"
    else:
        return "🔴 偏空（普遍走跌，建議多看少做）"


def format_messages(
    signals: list[dict[str, Any]],
    watchlist: Optional[list[dict[str, Any]]] = None,
    market: Optional[dict[str, Any]] = None,
    night_note: Optional[str] = None,
) -> list[str]:
    """產出乾淨、層次分明、同檔去重的 Telegram 選股情報（精簡為 1~2 則）。"""
    today = datetime.now().strftime("%Y/%m/%d")
    clean_signals = _deduplicate_signals(signals)

    buys = [s for s in clean_signals if s.get("action") == "BUY"]
    watches = [s for s in clean_signals if s.get("action") == "WATCH"]

    buys.sort(key=lambda x: -x.get("signal_score", 0))
    watches.sort(key=lambda x: -x.get("signal_score", 0))

    messages = []
    scan_count = len(watchlist) if watchlist else len(clean_signals)

    # ==================== 訊息 ①：每日核心決策情報 ====================
    msg1 = [
        f"📊 *台股每日決策快報* ｜ {today}",
        f"掃描 {scan_count} 檔 ➔ 🟢 *BUY ({len(buys)})* ｜ 🟡 *WATCH ({len(watches)})*",
        "─────────────────",
    ]

    # 1. 大盤與市場環境定調
    msg1.append("🎯 *大盤與環境定調*")
    if market and market.get("note"):
        msg1.append(f"• 大盤：{market['note']}")
    if night_note:
        msg1.append(f"• 夜盤：{night_note}")
    msg1.append(f"• 氛圍：{_market_sentiment(clean_signals)}")
    msg1.append("─────────────────")

    # 2. BUY 推薦進場清單（緊湊卡片）
    if buys:
        msg1.append(f"🟢 *【推薦進場名單】({len(buys)}檔)*\n")
        for s in buys:
            c = s.get("components", {})
            strat_desc = "/".join(s.get("matched_strats", ["預設"]))
            wr = f"{c['backtest_winrate']*100:.0f}%" if c.get("backtest_winrate") else "—"

            msg1.append(f"🔥 *{s['stock_id']} {s['name']}* （{strat_desc} ｜ 評分: {s['signal_score']}）")
            msg1.append(
                f"  進場 `{s.get('entry_price', 0)}` ➔ 損 `{s.get('stop_loss_price', 0)}` ｜ "
                f"標 `{s.get('target_price', 0)}` ｜ 風報比 `1:{s.get('risk_reward_ratio', 2.0)}`"
            )
            triggers = ", ".join(c.get("tech_signals", [])) or "多頭指標"
            msg1.append(f"  勝率 `{wr}` ｜ 亮點: {triggers}")
            if s.get("risk_notes"):
                msg1.append(f"  ⚠️ 風險: {' / '.join(s['risk_notes'][:2])}")
            msg1.append("")
    else:
        msg1.append("🟢 *【推薦進場名單】*\n今日無符合三關全過之 BUY 標的，建議保留現金觀望。\n")

    # 3. WATCH 重點觀察名單（精簡單行列表，最多 5 檔）
    if watches:
        msg1.append("─────────────────")
        top_w = watches[:5]
        msg1.append(f"🟡 *【重點觀察名單 TOP {len(top_w)}】*")
        for s in top_w:
            diff = _explain_why(s)
            msg1.append(f"• *{s['stock_id']} {s['name']}* ({s.get('signal_score', 0)}分) ↳ _{diff}_")
        msg1.append("")

    messages.append("\n".join(msg1))

    # ==================== 訊息 ②：量價異動特別警示（有重點才發送） ====================
    danger_stocks = [
        s for s in clean_signals
        if "放量滯漲" in s.get("components", {}).get("volume_patterns", [])
    ]
    breakout_stocks = [
        s for s in clean_signals
        if "倍量柱" in s.get("components", {}).get("volume_patterns", []) and s.get("action") in ("BUY", "WATCH")
    ]

    if danger_stocks or breakout_stocks:
        msg2 = ["🔬 *【主力籌碼與量價異常警示】*", "─────────────────"]
        if breakout_stocks:
            msg2.append("🚀 *主力點火倍量突破：*")
            for s in breakout_stocks[:5]:
                msg2.append(f"• *{s['stock_id']} {s['name']}* (量增價揚，留意續攻力道)")
            msg2.append("")
        if danger_stocks:
            msg2.append("⚠️ *高檔放量滯漲（防主力出貨）：*")
            for s in danger_stocks[:5]:
                msg2.append(f"• *{s['stock_id']} {s['name']}* (爆量不漲，嚴禁追高)")
            msg2.append("")
        messages.append("\n".join(msg2))

    return messages


def format_premarket(night: Optional[dict[str, Any]], signals: list[dict[str, Any]]) -> str:
    """夜盤盤前快報：夜盤方向預判 + 疊加昨日 BUY/WATCH 訊號（去重精準版）。"""
    from .night_session import tailwind_tag, bias_guidance

    today = datetime.now()
    wd = "一二三四五六日"[today.weekday()]
    lines = [f"🌙 *夜盤盤前快報* ｜ {today.strftime('%Y/%m/%d')} (週{wd})", "─────────────────"]

    # 1. 夜盤方向預判
    if night:
        lines.append(
            f"{night.get('emoji', '🌙')} *台指期夜盤 {night.get('pct', 0):+.2f}% "
            f"({night.get('spread', 0):+.0f} 點)*"
        )
        lines.append(f"近月收 `{night.get('close', 0):.0f}` ｜ 量 `{night.get('volume', 0):,}`")
        if night.get("date") != today.strftime("%Y-%m-%d"):
            lines.append(f"_（資料時間：{night.get('date')} 夜盤）_")
        lines.append(f"📈 開盤預判：*{night.get('label', '')}* ➔ {night.get('direction', '')}")
    else:
        lines.append("⚠️ 夜盤資料暫時取不到，今日盤前以個股訊號為主。")
    lines.append("─────────────────")

    # 2. 疊加昨日訊號（進行去重）
    clean_signals = _deduplicate_signals(signals)
    bias = night.get("bias", "flat") if night else "flat"
    tag = tailwind_tag(bias)

    actionable = [
        s for s in clean_signals
        if str(s.get("action", "")).upper() in ("BUY", "WATCH")
    ]

    if actionable:
        latest_day = actionable[0].get("date", "")
        batch = [s for s in actionable if s.get("date", "") == latest_day]
        buys = [s for s in batch if str(s.get("action", "")).upper() == "BUY"]
        watches = [s for s in batch if str(s.get("action", "")).upper() == "WATCH"]

        lines.append(f"📋 *昨日訊號 × 開盤校準* ({latest_day})")
        for s in (buys + watches)[:8]:
            act = str(s.get("action", "")).upper()
            dot = "🟢" if act == "BUY" else "🟡"
            lines.append(
                f"{dot} *{act} {s.get('stock_id', '')} {s.get('name', '')}* "
                f"({s.get('signal_score', '')}分) · {tag}"
            )
        lines.append(f"\n💡 _{bias_guidance(bias)}_")
    else:
        lines.append("📋 昨日無 BUY/WATCH 標的。")

    return "\n".join(lines)


def format_message(signals: list[dict[str, Any]]) -> str:
    """向後相容舊版單一訊息接口。"""
    msgs = format_messages(signals)
    return msgs[0] if msgs else ""
