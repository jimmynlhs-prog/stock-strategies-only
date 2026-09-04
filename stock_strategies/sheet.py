import json
import logging
import os
from typing import Any, Optional

import gspread
from google.oauth2.service_account import Credentials

from stock_strategies.storage import (
    load_latest_signals_local,
    load_performance_local,
    load_watchlist_local,
    save_performance_local,
    save_signals_local,
    save_watchlist_local,
)

logger = logging.getLogger(__name__)


def get_gsheet():
    creds_json = os.environ.get("GOOGLE_CREDS_JSON")
    sheet_id = os.environ.get("GOOGLE_SHEET_ID")
    if not creds_json or not sheet_id:
        raise ValueError("缺少 GOOGLE_CREDS_JSON 或 GOOGLE_SHEET_ID 環境變數")

    creds_dict = json.loads(creds_json)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    gc = gspread.authorize(creds)
    return gc.open_by_key(sheet_id)


def read_watchlist() -> list[dict[str, Any]]:
    """從 Google Sheet Watchlist 分頁讀股票清單（支援本地降級與雙軌同步）"""
    try:
        sh = get_gsheet()
        ws = sh.worksheet("Watchlist")
        rows = ws.get_all_records()
        if rows:
            # 同步更新本地儲存
            save_watchlist_local(rows)
            enabled = [
                r for r in rows
                if str(r.get("enabled", "")).upper() in ("TRUE", "1", "YES")
            ]
            return enabled
    except Exception as e:
        logger.warning("無法從 Google Sheet 讀取 Watchlist，切換至本地儲存: %s", e)

    # 降級從本地 SQLite / watchlist.json 讀取
    return load_watchlist_local(only_enabled=True)


SIGNALS_HEADERS = [
    "date", "strategy_id", "stock_id", "name", "action", "signal_score",
    "entry_price", "stop_loss_price", "target_price",
    "rr_ratio", "position_pct", "winrate", "samples",
    "tech_signals", "risk_notes"
]


def append_signals(signals: list[dict[str, Any]]):
    """把結果寫回 Signals 分頁（同時同步寫入本地 SQLite 與 data/signals.csv）"""
    if not signals:
        return

    # 1. 雙軌備份：優先保證本地 SQLite 與 CSV 寫入成功
    try:
        save_signals_local(signals)
    except Exception as e:
        logger.error("寫入本地 Signals 備份失敗: %s", e)

    # 2. 同步寫回 Google Sheet
    try:
        sh = get_gsheet()
        try:
            ws = sh.worksheet("Signals")
            values = ws.get_all_values()
            if not values:
                headers = SIGNALS_HEADERS
                ws.append_row(headers)
            else:
                headers = [h.strip() for h in values[0]]
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(title="Signals", rows=2000, cols=20)
            headers = SIGNALS_HEADERS
            ws.append_row(headers)

        has_strat = "strategy_id" in headers
        rows = []
        for s in signals:
            c = s.get("components", {})
            if has_strat:
                rows.append([
                    s.get("date", ""),
                    s.get("strategy_id", "default"),
                    s.get("stock_id", ""),
                    s.get("name", ""),
                    s.get("action", ""),
                    s.get("signal_score", ""),
                    s.get("entry_price", ""),
                    s.get("stop_loss_price", ""),
                    s.get("target_price", ""),
                    s.get("risk_reward_ratio", ""),
                    s.get("position_size_pct", ""),
                    c.get("backtest_winrate", ""),
                    c.get("backtest_samples", ""),
                    ", ".join(c.get("tech_signals", [])),
                    " / ".join(s.get("risk_notes", [])),
                ])
            else:
                rows.append([
                    s.get("date", ""),
                    s.get("stock_id", ""),
                    s.get("name", ""),
                    s.get("action", ""),
                    s.get("signal_score", ""),
                    s.get("entry_price", ""),
                    s.get("stop_loss_price", ""),
                    s.get("target_price", ""),
                    s.get("risk_reward_ratio", ""),
                    s.get("position_size_pct", ""),
                    c.get("backtest_winrate", ""),
                    c.get("backtest_samples", ""),
                    ", ".join(c.get("tech_signals", [])),
                    " / ".join(s.get("risk_notes", [])),
                ])
        ws.append_rows(rows)
    except Exception as e:
        logger.warning("同步寫入 Google Sheet Signals 失敗（本地已保存）: %s", e)


def _ensure_watchlist_headers(ws) -> list[str]:
    """讀第一列 headers，沒 headers 就建好 stock_id/name/enabled 三欄。"""
    values = ws.get_all_values()
    if not values:
        headers = ["stock_id", "name", "enabled"]
        ws.append_row(headers)
        return headers
    headers = [h.strip() for h in values[0]]
    if "stock_id" not in headers or "enabled" not in headers:
        return headers
    return headers


def add_to_watchlist(stock_id: str, name: str = "") -> dict[str, Any]:
    """加一檔到 Watchlist（雙軌同步 Google Sheet 與本地儲存）"""
    res = {"status": "added", "stock_id": stock_id, "name": name}
    try:
        sh = get_gsheet()
        ws = sh.worksheet("Watchlist")
        headers = _ensure_watchlist_headers(ws)

        sid_col = headers.index("stock_id") + 1
        name_col = headers.index("name") + 1 if "name" in headers else None
        en_col = headers.index("enabled") + 1

        rows = ws.get_all_records()
        found = False
        for i, r in enumerate(rows, start=2):
            if str(r.get("stock_id", "")).strip() == str(stock_id).strip():
                found = True
                current = str(r.get("enabled", "")).upper()
                if current in ("TRUE", "1", "YES"):
                    res = {
                        "status": "exists",
                        "stock_id": stock_id,
                        "name": r.get("name", name),
                    }
                else:
                    ws.update_cell(i, en_col, "TRUE")
                    res = {
                        "status": "reenabled",
                        "stock_id": stock_id,
                        "name": r.get("name", name),
                    }
                break

        if not found:
            new_row = [""] * len(headers)
            new_row[sid_col - 1] = str(stock_id)
            if name_col is not None:
                new_row[name_col - 1] = name
            new_row[en_col - 1] = "TRUE"
            ws.append_row(new_row)
    except Exception as e:
        logger.warning("無法同步寫入 Google Sheet Watchlist: %s", e)

    # 同步寫入本地
    save_watchlist_local([{"stock_id": stock_id, "name": name, "enabled": True}])
    return res


def remove_from_watchlist(stock_id: str) -> dict[str, Any]:
    """把 Watchlist 該 stock_id 的 enabled 改成 FALSE（雙軌同步）"""
    res = {"status": "disabled", "stock_id": stock_id}
    try:
        sh = get_gsheet()
        ws = sh.worksheet("Watchlist")
        headers = _ensure_watchlist_headers(ws)
        if "enabled" in headers:
            en_col = headers.index("enabled") + 1
            rows = ws.get_all_records()
            for i, r in enumerate(rows, start=2):
                if str(r.get("stock_id", "")).strip() == str(stock_id).strip():
                    ws.update_cell(i, en_col, "FALSE")
                    break
    except Exception as e:
        logger.warning("無法同步修改 Google Sheet Watchlist: %s", e)

    save_watchlist_local([{"stock_id": stock_id, "enabled": False}])
    return res


def read_latest_signals(limit: int = 50) -> list[dict[str, Any]]:
    """從 Signals 讀最近 N 筆紀錄（優先 Google Sheet，失敗降級本地 SQLite）"""
    try:
        sh = get_gsheet()
        ws = sh.worksheet("Signals")
        rows = ws.get_all_records()
        if rows:
            return rows[-limit:][::-1]
    except Exception as e:
        logger.warning("無法從 Google Sheet 讀取最新 Signals，切換至本地儲存: %s", e)

    return load_latest_signals_local(limit=limit)


PERFORMANCE_HEADERS = [
    "signal_date", "strategy_id", "stock_id", "name", "entry_close", "entry_open",
    "t5_date", "t5_close", "t5_ret",
    "t10_date", "t10_close", "t10_ret",
    "t20_date", "t20_close", "t20_ret",
    "hit_target", "hit_stop", "status",
]


def read_performance() -> list[dict[str, Any]]:
    """讀取 Performance 紀錄（優先 Google Sheet，失敗降級本地 SQLite）"""
    try:
        sh = get_gsheet()
        ws = sh.worksheet("Performance")
        rows = ws.get_all_records()
        if rows:
            return rows
    except Exception as e:
        logger.warning("無法從 Google Sheet 讀取 Performance，切換至本地儲存: %s", e)

    return load_performance_local()


def write_performance(records: list[dict[str, Any]]):
    """整張 Performance 清空重寫（同時同步寫入本地 SQLite 與 data/performance.csv）"""
    # 1. 本地雙軌備份
    try:
        save_performance_local(records)
    except Exception as e:
        logger.error("寫入本地 Performance 備份失敗: %s", e)

    # 2. 寫入 Google Sheet
    try:
        sh = get_gsheet()
        try:
            ws = sh.worksheet("Performance")
            ws.clear()
        except gspread.WorksheetNotFound:
            rows_alloc = max(2000, len(records) + 100)
            ws = sh.add_worksheet(
                title="Performance", rows=rows_alloc, cols=len(PERFORMANCE_HEADERS)
            )

        ws.append_row(PERFORMANCE_HEADERS)
        if not records:
            return

        rows = [[r.get(h, "") for h in PERFORMANCE_HEADERS] for r in records]
        ws.append_rows(rows)
    except Exception as e:
        logger.warning("同步寫入 Google Sheet Performance 失敗（本地已保存）: %s", e)

