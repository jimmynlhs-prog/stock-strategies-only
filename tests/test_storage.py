import csv
import json
import sqlite3
import pytest
from pathlib import Path

from stock_strategies.storage import (
    get_db_connection,
    init_db,
    save_watchlist_local,
    load_watchlist_local,
    save_signals_local,
    load_latest_signals_local,
    save_performance_local,
    load_performance_local,
    export_watchlist_json,
    export_signals_csv,
    export_performance_csv,
)


@pytest.fixture
def memory_db() -> sqlite3.Connection:
    """提供記憶體測試資料庫"""
    conn = get_db_connection(":memory:")
    init_db(conn)
    yield conn
    conn.close()


def test_watchlist_crud_and_json_export(memory_db: sqlite3.Connection, tmp_path: Path):
    sample_watchlist = [
        {"stock_id": "2330", "name": "台積電", "enabled": True},
        {"stock_id": "2317", "name": "鴻海", "enabled": False},
        {"stock_id": "2454", "name": "聯發科", "enabled": "TRUE"},
    ]
    save_watchlist_local(sample_watchlist, conn=memory_db, export_json=False)

    # 讀取啟用的股票
    enabled_stocks = load_watchlist_local(conn=memory_db, only_enabled=True)
    assert len(enabled_stocks) == 2
    assert {s["stock_id"] for s in enabled_stocks} == {"2330", "2454"}

    # 讀取全部股票
    all_stocks = load_watchlist_local(conn=memory_db, only_enabled=False)
    assert len(all_stocks) == 3

    # 測試 JSON 匯出
    json_path = tmp_path / "watchlist.json"
    export_watchlist_json(memory_db, out_path=json_path)
    assert json_path.exists()
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
        assert len(data) == 3


def test_signals_crud_and_csv_export(memory_db: sqlite3.Connection, tmp_path: Path):
    sample_signals = [
        {
            "date": "2026-09-04",
            "strategy_id": "chan_shortterm",
            "stock_id": "2330",
            "name": "台積電",
            "action": "BUY",
            "signal_score": 85.5,
            "entry_price": 950.0,
            "stop_loss_price": 900.0,
            "target_price": 1050.0,
            "risk_reward_ratio": 2.0,
            "position_size_pct": 0.15,
            "components": {
                "backtest_winrate": 0.75,
                "backtest_samples": 20,
                "tech_signals": ["MA_ALIGN", "VOLUME_BREAK"],
            },
            "risk_notes": ["注意大盤壓力"],
        },
        {
            "date": "2026-09-04",
            "strategy_id": "chan_longterm",
            "stock_id": "2454",
            "name": "聯發科",
            "action": "WATCH",
            "signal_score": 62.0,
            "entry_price": 1200.0,
            "stop_loss_price": 1100.0,
            "target_price": 1400.0,
            "risk_reward_ratio": 2.0,
            "position_size_pct": 0.10,
            "components": {
                "backtest_winrate": 0.60,
                "backtest_samples": 15,
                "tech_signals": ["BOLLINGER_BOUNCE"],
            },
            "risk_notes": [],
        }
    ]
    save_signals_local(sample_signals, conn=memory_db, export_csv=False)

    loaded = load_latest_signals_local(limit=10, conn=memory_db)
    assert len(loaded) == 2
    assert loaded[0]["stock_id"] in ("2330", "2454")
    assert loaded[0]["signal_score"] > 0

    # 測試 CSV 匯出
    csv_path = tmp_path / "signals.csv"
    export_signals_csv(memory_db, out_path=csv_path)
    assert csv_path.exists()
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        assert len(rows) == 2
        assert rows[0]["strategy_id"] in ("chan_shortterm", "chan_longterm")


def test_performance_crud_and_csv_export(memory_db: sqlite3.Connection, tmp_path: Path):
    sample_records = [
        {
            "signal_date": "2026-08-01",
            "strategy_id": "default",
            "stock_id": "2330",
            "name": "台積電",
            "entry_close": 900.0,
            "entry_open": 895.0,
            "t5_date": "2026-08-08",
            "t5_close": 920.0,
            "t5_ret": 2.22,
            "t10_date": "2026-08-15",
            "t10_close": 950.0,
            "t10_ret": 5.56,
            "t20_date": "2026-08-29",
            "t20_close": 990.0,
            "t20_ret": 10.0,
            "hit_target": 1,
            "hit_stop": 0,
            "status": "closed",
        }
    ]
    save_performance_local(sample_records, conn=memory_db, export_csv=False)

    loaded = load_performance_local(conn=memory_db)
    assert len(loaded) == 1
    assert loaded[0]["stock_id"] == "2330"
    assert loaded[0]["hit_target"] == 1

    # 測試 CSV 匯出
    csv_path = tmp_path / "performance.csv"
    export_performance_csv(memory_db, out_path=csv_path)
    assert csv_path.exists()
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        assert len(rows) == 1
        assert rows[0]["stock_id"] == "2330"
