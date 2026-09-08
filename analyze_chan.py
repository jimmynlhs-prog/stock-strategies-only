"""
纏論策略即時分析腳本
掃描 Watchlist 內的所有股票，分別評估：
1. 纏論一買 (chan_longterm)：長線波段策略（底背馳轉折、布林下軌反彈）
2. 纏論三買 (chan_shortterm)：短線爆發策略（突破中樞強勢回抽、均線多頭）
"""

import json
import os
import sys
from dotenv import load_dotenv

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

load_dotenv()


from stock_strategies.sheet import read_watchlist
from stock_strategies.loader import get_strategy
from stock_strategies.evaluate import evaluate


def run_chan_analysis():
    print("=" * 65)
    print("📊 正在啟動專案【纏論量化策略】即時深度分析...")
    print("=" * 65)

    watchlist = read_watchlist()
    print(f"📋 監控股票池共 {len(watchlist)} 檔：")
    for w in watchlist:
        print(f"   - {w.get('stock_id')} {w.get('name', '')}")
    print("-" * 65)

    chan_strategies = [
        ("chan_longterm", "🎯 纏論一買 (長線波段)"),
        ("chan_shortterm", "🚀 纏論三買 (短線爆發)"),
    ]

    all_results = []
    for s_idx, (s_id, s_title) in enumerate(chan_strategies, 1):
        strat = get_strategy(s_id)
        if not strat:
            print(f"❌ 找不到策略設定檔: {s_id}")
            continue

        print(f"\n【策略 {s_idx}】{s_title}")
        print(f"說明: {strat.get('description', '')}")
        print("-" * 65)

        for w in watchlist:
            sid = str(w["stock_id"])
            name = str(w.get("name", ""))
            try:
                res = evaluate(sid, name, strategy=strat)
                if res:
                    all_results.append((s_id, s_title, res))
                    action = res.get("action", "SKIP")
                    score = res.get("signal_score", 0)
                    price = res.get("entry_price", 0)
                    target = res.get("target_price", 0)
                    stop = res.get("stop_loss_price", 0)
                    rr = res.get("risk_reward_ratio", 0)
                    tech_sigs = res.get("components", {}).get("tech_signals", [])

                    action_badge = "🟢 BUY" if action == "BUY" else ("🟡 WATCH" if action == "WATCH" else "⚪ SKIP")
                    print(f"[{action_badge}] {sid} {name:<6} ｜ 評分: {score:4.1f} ｜ 價格: {price:6.1f} (目標: {target:6.1f} / 停損: {stop:6.1f}, 風報比: {rr:.2f})")
                    if tech_sigs:
                        print(f"       技術訊號: {', '.join(tech_sigs)}")
                    if res.get("risk_notes"):
                        print(f"       風險提示: {', '.join(res.get('risk_notes', []))}")
            except Exception as e:
                print(f"❌ 分析 {sid} {name} 發生錯誤: {e}")

    # 匯總輸出 JSON 供進一步檢視
    summary_data = []
    for s_id, s_title, res in all_results:
        summary_data.append({
            "strategy_id": s_id,
            "strategy_title": s_title,
            "stock_id": res.get("stock_id"),
            "name": res.get("name"),
            "action": res.get("action"),
            "signal_score": res.get("signal_score"),
            "entry_price": res.get("entry_price"),
            "stop_loss_price": res.get("stop_loss_price"),
            "target_price": res.get("target_price"),
            "risk_reward_ratio": res.get("risk_reward_ratio"),
            "components": res.get("components"),
            "risk_notes": res.get("risk_notes"),
        })

    def default_serializer(o):
        if hasattr(o, "item"):
            return o.item()
        if hasattr(o, "tolist"):
            return o.tolist()
        return str(o)

    with open("data/chan_analysis_latest.json", "w", encoding="utf-8") as f:
        json.dump(summary_data, f, ensure_ascii=False, indent=2, default=default_serializer)

    print("\n" + "=" * 65)
    print("✅ 纏論策略分析完成！結果已儲存至 data/chan_analysis_latest.json")
    print("=" * 65)



if __name__ == "__main__":
    run_chan_analysis()
