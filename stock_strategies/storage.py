import csv
import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

DEFAULT_DATA_DIR = Path("data")
DEFAULT_DB_PATH = DEFAULT_DATA_DIR / "stock_data.db"


def get_db_connection(db_path: Optional[str | Path] = None) -> sqlite3.Connection:
    """建立並設定 SQLite 資料庫連線，設定 WAL 模式與 Row 工廠。"""
    if db_path is None:
        DEFAULT_DATA_DIR.mkdir(parents=True, exist_ok=True)
        target_path = str(DEFAULT_DB_PATH)
    else:
        target_path = str(db_path)

    try:
        conn = sqlite3.connect(target_path)
        conn.row_factory = sqlite3.Row
        if target_path != ":memory:":
            conn.execute("PRAGMA journal_mode=WAL;")
        return conn
    except sqlite3.Error as e:
        logger.error("無法連線至 SQLite 資料庫 (%s): %s", target_path, e)
        raise


def init_db(conn: sqlite3.Connection) -> None:
    """初始化資料庫表格與索引 (Schema)。"""
    try:
        with conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS watchlist (
                    stock_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    strategy_id TEXT NOT NULL DEFAULT 'default',
                    stock_id TEXT NOT NULL,
                    name TEXT NOT NULL DEFAULT '',
                    action TEXT NOT NULL DEFAULT '',
                    signal_score REAL DEFAULT 0.0,
                    entry_price REAL DEFAULT 0.0,
                    stop_loss_price REAL DEFAULT 0.0,
                    target_price REAL DEFAULT 0.0,
                    rr_ratio REAL DEFAULT 0.0,
                    position_pct REAL DEFAULT 0.0,
                    winrate REAL DEFAULT 0.0,
                    samples INTEGER DEFAULT 0,
                    tech_signals TEXT DEFAULT '',
                    risk_notes TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(date, strategy_id, stock_id)
                );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_signals_date ON signals(date);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_signals_stock ON signals(stock_id);")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS performance (
                    signal_date TEXT NOT NULL,
                    strategy_id TEXT NOT NULL DEFAULT 'default',
                    stock_id TEXT NOT NULL,
                    name TEXT NOT NULL DEFAULT '',
                    entry_close REAL DEFAULT 0.0,
                    entry_open REAL DEFAULT 0.0,
                    t5_date TEXT DEFAULT '',
                    t5_close REAL DEFAULT 0.0,
                    t5_ret REAL DEFAULT 0.0,
                    t10_date TEXT DEFAULT '',
                    t10_close REAL DEFAULT 0.0,
                    t10_ret REAL DEFAULT 0.0,
                    t20_date TEXT DEFAULT '',
                    t20_close REAL DEFAULT 0.0,
                    t20_ret REAL DEFAULT 0.0,
                    hit_target INTEGER DEFAULT 0,
                    hit_stop INTEGER DEFAULT 0,
                    status TEXT DEFAULT '',
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(signal_date, strategy_id, stock_id)
                );
            """)
        logger.info("SQLite 資料庫 Schema 初始化成功。")
    except sqlite3.Error as e:
        logger.error("初始化 SQLite Schema 失敗: %s", e)
        raise


def save_watchlist_local(
    watchlist: list[dict[str, Any]],
    conn: Optional[sqlite3.Connection] = None,
    export_json: bool = True,
) -> None:
    """將監控清單儲存至本地 SQLite 與 data/watchlist.json。"""
    close_conn = False
    if conn is None:
        conn = get_db_connection()
        init_db(conn)
        close_conn = True

    try:
        with conn:
            for item in watchlist:
                sid = str(item.get("stock_id", "")).strip()
                if not sid:
                    continue
                name = str(item.get("name", "")).strip()
                enabled_val = item.get("enabled", True)
                if isinstance(enabled_val, str):
                    enabled_int = 1 if enabled_val.upper() in ("TRUE", "1", "YES") else 0
                else:
                    enabled_int = 1 if bool(enabled_val) else 0

                conn.execute("""
                    INSERT INTO watchlist (stock_id, name, enabled, updated_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(stock_id) DO UPDATE SET
                        name = excluded.name,
                        enabled = excluded.enabled,
                        updated_at = CURRENT_TIMESTAMP;
                """, (sid, name, enabled_int))

        if export_json:
            export_watchlist_json(conn)
    except sqlite3.Error as e:
        logger.error("儲存 Watchlist 至本地資料庫失敗: %s", e)
        raise
    finally:
        if close_conn:
            conn.close()


def load_watchlist_local(
    conn: Optional[sqlite3.Connection] = None,
    only_enabled: bool = True,
) -> list[dict[str, Any]]:
    """從本地 SQLite 或 data/watchlist.json 讀取監控清單。"""
    close_conn = False
    if conn is None:
        conn = get_db_connection()
        init_db(conn)
        close_conn = True

    try:
        cursor = conn.cursor()
        query = "SELECT stock_id, name, enabled FROM watchlist"
        if only_enabled:
            query += " WHERE enabled = 1"
        query += " ORDER BY stock_id ASC"

        cursor.execute(query)
        rows = cursor.fetchall()
        result = [
            {
                "stock_id": str(r["stock_id"]),
                "name": str(r["name"]),
                "enabled": bool(r["enabled"]),
            }
            for r in rows
        ]
        cursor.close()

        # 若資料庫為空但存在 JSON 檔，從 JSON 補充
        if not result and (DEFAULT_DATA_DIR / "watchlist.json").exists():
            with open(DEFAULT_DATA_DIR / "watchlist.json", "r", encoding="utf-8") as f:
                json_data = json.load(f)
                if only_enabled:
                    return [d for d in json_data if d.get("enabled", True)]
                return json_data

        return result
    except sqlite3.Error as e:
        logger.error("讀取本地 Watchlist 失敗: %s", e)
        return []
    finally:
        if close_conn:
            conn.close()


def save_signals_local(
    signals: list[dict[str, Any]],
    conn: Optional[sqlite3.Connection] = None,
    export_csv: bool = True,
) -> None:
    """將選股訊號寫入本地 SQLite 與 data/signals.csv。"""
    if not signals:
        return

    close_conn = False
    if conn is None:
        conn = get_db_connection()
        init_db(conn)
        close_conn = True

    try:
        with conn:
            for s in signals:
                c = s.get("components", {})
                date_val = str(s.get("date", "")).strip()
                strat_id = str(s.get("strategy_id", "default")).strip()
                sid = str(s.get("stock_id", "")).strip()
                if not date_val or not sid:
                    continue

                tech_signals = ", ".join(c.get("tech_signals", [])) if isinstance(c.get("tech_signals"), list) else str(c.get("tech_signals", ""))
                risk_notes = " / ".join(s.get("risk_notes", [])) if isinstance(s.get("risk_notes"), list) else str(s.get("risk_notes", ""))

                conn.execute("""
                    INSERT INTO signals (
                        date, strategy_id, stock_id, name, action, signal_score,
                        entry_price, stop_loss_price, target_price, rr_ratio,
                        position_pct, winrate, samples, tech_signals, risk_notes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(date, strategy_id, stock_id) DO UPDATE SET
                        name = excluded.name,
                        action = excluded.action,
                        signal_score = excluded.signal_score,
                        entry_price = excluded.entry_price,
                        stop_loss_price = excluded.stop_loss_price,
                        target_price = excluded.target_price,
                        rr_ratio = excluded.rr_ratio,
                        position_pct = excluded.position_pct,
                        winrate = excluded.winrate,
                        samples = excluded.samples,
                        tech_signals = excluded.tech_signals,
                        risk_notes = excluded.risk_notes;
                """, (
                    date_val,
                    strat_id,
                    sid,
                    str(s.get("name", "")),
                    str(s.get("action", "")),
                    float(s.get("signal_score") or 0.0),
                    float(s.get("entry_price") or 0.0),
                    float(s.get("stop_loss_price") or 0.0),
                    float(s.get("target_price") or 0.0),
                    float(s.get("risk_reward_ratio") or 0.0),
                    float(s.get("position_size_pct") or 0.0),
                    float(c.get("backtest_winrate") or 0.0),
                    int(c.get("backtest_samples") or 0),
                    tech_signals,
                    risk_notes,
                ))

        if export_csv:
            export_signals_csv(conn)
    except sqlite3.Error as e:
        logger.error("寫入本地 Signals 資料庫失敗: %s", e)
        raise
    finally:
        if close_conn:
            conn.close()


def load_latest_signals_local(
    limit: int = 50,
    conn: Optional[sqlite3.Connection] = None,
) -> list[dict[str, Any]]:
    """從本地 SQLite 讀取最近 N 筆訊號。"""
    close_conn = False
    if conn is None:
        conn = get_db_connection()
        init_db(conn)
        close_conn = True

    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT date, strategy_id, stock_id, name, action, signal_score,
                   entry_price, stop_loss_price, target_price, rr_ratio,
                   position_pct, winrate, samples, tech_signals, risk_notes
            FROM signals
            ORDER BY date DESC, id DESC
            LIMIT ?
        """, (limit,))
        rows = cursor.fetchall()
        result = [dict(r) for r in rows]
        cursor.close()
        return result
    except sqlite3.Error as e:
        logger.error("讀取本地最新 Signals 失敗: %s", e)
        return []
    finally:
        if close_conn:
            conn.close()


def save_performance_local(
    records: list[dict[str, Any]],
    conn: Optional[sqlite3.Connection] = None,
    export_csv: bool = True,
) -> None:
    """將 Performance 追蹤紀錄寫入本地 SQLite 與 data/performance.csv。"""
    close_conn = False
    if conn is None:
        conn = get_db_connection()
        init_db(conn)
        close_conn = True

    try:
        with conn:
            for r in records:
                sig_date = str(r.get("signal_date", "")).strip()
                strat_id = str(r.get("strategy_id", "default")).strip()
                sid = str(r.get("stock_id", "")).strip()
                if not sig_date or not sid:
                    continue

                conn.execute("""
                    INSERT INTO performance (
                        signal_date, strategy_id, stock_id, name, entry_close, entry_open,
                        t5_date, t5_close, t5_ret,
                        t10_date, t10_close, t10_ret,
                        t20_date, t20_close, t20_ret,
                        hit_target, hit_stop, status, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(signal_date, strategy_id, stock_id) DO UPDATE SET
                        name = excluded.name,
                        entry_close = excluded.entry_close,
                        entry_open = excluded.entry_open,
                        t5_date = excluded.t5_date,
                        t5_close = excluded.t5_close,
                        t5_ret = excluded.t5_ret,
                        t10_date = excluded.t10_date,
                        t10_close = excluded.t10_close,
                        t10_ret = excluded.t10_ret,
                        t20_date = excluded.t20_date,
                        t20_close = excluded.t20_close,
                        t20_ret = excluded.t20_ret,
                        hit_target = excluded.hit_target,
                        hit_stop = excluded.hit_stop,
                        status = excluded.status,
                        updated_at = CURRENT_TIMESTAMP;
                """, (
                    sig_date,
                    strat_id,
                    sid,
                    str(r.get("name", "")),
                    float(r.get("entry_close") or 0.0),
                    float(r.get("entry_open") or 0.0),
                    str(r.get("t5_date", "")),
                    float(r.get("t5_close") or 0.0),
                    float(r.get("t5_ret") or 0.0),
                    str(r.get("t10_date", "")),
                    float(r.get("t10_close") or 0.0),
                    float(r.get("t10_ret") or 0.0),
                    str(r.get("t20_date", "")),
                    float(r.get("t20_close") or 0.0),
                    float(r.get("t20_ret") or 0.0),
                    1 if str(r.get("hit_target", "")).upper() in ("1", "TRUE", "YES") else 0,
                    1 if str(r.get("hit_stop", "")).upper() in ("1", "TRUE", "YES") else 0,
                    str(r.get("status", "")),
                ))

        if export_csv:
            export_performance_csv(conn)
    except sqlite3.Error as e:
        logger.error("儲存本地 Performance 失敗: %s", e)
        raise
    finally:
        if close_conn:
            conn.close()


def load_performance_local(
    conn: Optional[sqlite3.Connection] = None,
) -> list[dict[str, Any]]:
    """從本地 SQLite 讀取全部績效追蹤紀錄。"""
    close_conn = False
    if conn is None:
        conn = get_db_connection()
        init_db(conn)
        close_conn = True

    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT signal_date, strategy_id, stock_id, name, entry_close, entry_open,
                   t5_date, t5_close, t5_ret,
                   t10_date, t10_close, t10_ret,
                   t20_date, t20_close, t20_ret,
                   hit_target, hit_stop, status
            FROM performance
            ORDER BY signal_date ASC
        """)
        rows = cursor.fetchall()
        result = [dict(r) for r in rows]
        cursor.close()
        return result
    except sqlite3.Error as e:
        logger.error("讀取本地 Performance 失敗: %s", e)
        return []
    finally:
        if close_conn:
            conn.close()


def export_watchlist_json(conn: sqlite3.Connection, out_path: Optional[Path] = None) -> None:
    """匯出 Watchlist 至 JSON 檔案以供 GitHub 網頁版查看。"""
    if out_path is None:
        DEFAULT_DATA_DIR.mkdir(parents=True, exist_ok=True)
        out_path = DEFAULT_DATA_DIR / "watchlist.json"

    cursor = conn.cursor()
    cursor.execute("SELECT stock_id, name, enabled FROM watchlist ORDER BY stock_id ASC")
    rows = [dict(r) for r in cursor.fetchall()]
    cursor.close()

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)


def export_signals_csv(conn: sqlite3.Connection, out_path: Optional[Path] = None) -> None:
    """匯出 Signals 至 CSV 檔案以供 GitHub 網頁版直接瀏覽。"""
    if out_path is None:
        DEFAULT_DATA_DIR.mkdir(parents=True, exist_ok=True)
        out_path = DEFAULT_DATA_DIR / "signals.csv"

    cursor = conn.cursor()
    cursor.execute("""
        SELECT date, strategy_id, stock_id, name, action, signal_score,
               entry_price, stop_loss_price, target_price, rr_ratio,
               position_pct, winrate, samples, tech_signals, risk_notes
        FROM signals
        ORDER BY date DESC, id DESC
    """)
    rows = cursor.fetchall()
    cursor.close()

    headers = [
        "date", "strategy_id", "stock_id", "name", "action", "signal_score",
        "entry_price", "stop_loss_price", "target_price", "rr_ratio",
        "position_pct", "winrate", "samples", "tech_signals", "risk_notes"
    ]

    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for r in rows:
            writer.writerow(dict(r))


def export_performance_csv(conn: sqlite3.Connection, out_path: Optional[Path] = None) -> None:
    """匯出 Performance 追蹤紀錄至 CSV 檔案以供 GitHub 網頁版直接瀏覽。"""
    if out_path is None:
        DEFAULT_DATA_DIR.mkdir(parents=True, exist_ok=True)
        out_path = DEFAULT_DATA_DIR / "performance.csv"

    cursor = conn.cursor()
    cursor.execute("""
        SELECT signal_date, strategy_id, stock_id, name, entry_close, entry_open,
               t5_date, t5_close, t5_ret,
               t10_date, t10_close, t10_ret,
               t20_date, t20_close, t20_ret,
               hit_target, hit_stop, status
        FROM performance
        ORDER BY signal_date ASC
    """)
    rows = cursor.fetchall()
    cursor.close()

    headers = [
        "signal_date", "strategy_id", "stock_id", "name", "entry_close", "entry_open",
        "t5_date", "t5_close", "t5_ret",
        "t10_date", "t10_close", "t10_ret",
        "t20_date", "t20_close", "t20_ret",
        "hit_target", "hit_stop", "status"
    ]

    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for r in rows:
            writer.writerow(dict(r))
