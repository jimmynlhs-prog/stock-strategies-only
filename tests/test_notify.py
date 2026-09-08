from stock_strategies.notify import (
    _deduplicate_signals,
    format_messages,
    format_premarket,
    format_message,
)


def test_deduplicate_signals_merges_strategies():
    raw_signals = [
        {
            "stock_id": "2330",
            "name": "台積電",
            "strategy_id": "default",
            "action": "WATCH",
            "signal_score": 60,
            "entry_price": 950,
        },
        {
            "stock_id": "2330",
            "name": "台積電",
            "strategy_id": "chan_shortterm",
            "action": "BUY",
            "signal_score": 85,
            "entry_price": 955,
            "stop_loss_price": 900,
            "target_price": 1100,
            "risk_reward_ratio": 2.6,
            "components": {"tech_signals": ["均線多頭", "MACD多頭"], "backtest_winrate": 0.75},
        },
        {
            "stock_id": "2454",
            "name": "聯發科",
            "strategy_id": "default",
            "action": "WATCH",
            "signal_score": 55,
        },
    ]

    deduped = _deduplicate_signals(raw_signals)
    assert len(deduped) == 2

    tsmc = next(s for s in deduped if s["stock_id"] == "2330")
    assert tsmc["action"] == "BUY"
    assert tsmc["signal_score"] == 85
    assert set(tsmc["matched_strats"]) == {"預設V3.2", "纏論三買"}


def test_format_messages_structure():
    signals = [
        {
            "stock_id": "2330",
            "name": "台積電",
            "strategy_id": "default",
            "action": "BUY",
            "signal_score": 88,
            "entry_price": 980.0,
            "stop_loss_price": 911.4,
            "target_price": 1127.0,
            "risk_reward_ratio": 2.14,
            "components": {
                "fundamental_pass": True,
                "tech_score": 90,
                "backtest_winrate": 0.72,
                "tech_signals": ["均線多頭", "帶量突破"],
                "volume_patterns": ["倍量柱"],
            },
            "trend": {"chg_5d": 3.5, "above_ma20": True},
        },
        {
            "stock_id": "2454",
            "name": "聯發科",
            "strategy_id": "default",
            "action": "WATCH",
            "signal_score": 58,
            "components": {
                "fundamental_pass": True,
                "tech_score": 45,
            },
            "trend": {"chg_5d": 1.2, "above_ma20": True},
        },
    ]

    market = {"note": "大盤多頭排列 (站上月季線)"}
    night_note = "夜盤 +0.8% (順風🟢)"

    msgs = format_messages(signals, market=market, night_note=night_note)
    assert len(msgs) >= 1

    # 檢查第一則內容
    main_msg = msgs[0]
    assert "台股每日決策快報" in main_msg
    assert "大盤：大盤多頭排列" in main_msg
    assert "2330 台積電" in main_msg
    assert "980.0" in main_msg
    assert "2454 聯發科" in main_msg

    # 檢查第二則量價解析 (因為有倍量柱)
    if len(msgs) > 1:
        assert "主力籌碼與量價異常警示" in msgs[1]
        assert "倍量突破" in msgs[1]


def test_format_premarket_dedup():
    night = {
        "pct": 1.25,
        "spread": 280,
        "close": 22800,
        "volume": 65000,
        "label": "大漲",
        "direction": "開高走高",
        "bias": "bull",
        "emoji": "🔥",
        "date": "2026-09-08",
    }
    signals = [
        {"date": "2026-09-07", "stock_id": "2330", "name": "台積電", "action": "BUY", "signal_score": 85},
        {"date": "2026-09-07", "stock_id": "2330", "name": "台積電", "action": "BUY", "signal_score": 80},
    ]

    pm_msg = format_premarket(night, signals)
    assert "夜盤盤前快報" in pm_msg
    assert "台指期夜盤 +1.25%" in pm_msg
    assert "2330 台積電" in pm_msg
    # 確保去重後 2330 只出現一次
    assert pm_msg.count("2330 台積電") == 1


def test_format_message_backward_compatibility():
    signals = [
        {"stock_id": "2330", "name": "台積電", "action": "BUY", "signal_score": 80}
    ]
    single = format_message(signals)
    assert "台股每日決策快報" in single
