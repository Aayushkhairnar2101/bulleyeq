import json
import sqlite3
from pathlib import Path
from contextlib import contextmanager
from .config import get_settings

def connect():
    path = Path(get_settings().database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    return db

def init_db():
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, at TEXT NOT NULL, kind TEXT NOT NULL, detail TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS decisions (id INTEGER PRIMARY KEY, at TEXT NOT NULL, product TEXT NOT NULL, signal TEXT NOT NULL, reason TEXT NOT NULL, indicators TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS baselines (day TEXT PRIMARY KEY, equity REAL NOT NULL, locked TEXT);
        CREATE TABLE IF NOT EXISTS orders (id TEXT PRIMARY KEY, client_id TEXT UNIQUE NOT NULL, product TEXT NOT NULL, side TEXT NOT NULL, size REAL NOT NULL, state TEXT NOT NULL, created_at TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS worker_lease (id INTEGER PRIMARY KEY CHECK(id=1), owner TEXT NOT NULL, expires_at REAL NOT NULL);
        """)
        defaults = {"bot_state": "stopped", "strategy": {"product":"BTCUSD","resolution":"5m","fast_ema":9,"slow_ema":21,"trend_sma":50,"rsi_period":14,"rsi_long":52,"rsi_short":48,"atr_period":14,"breakout_period":20,"volume_period":20,"vwap_period":20,"min_relative_volume":1.5,"atr_stop_multiplier":1.5,"risk_reward":2.0}, "risk": {"daily_loss_pct":2,"daily_profit_pct":3,"max_position_size":1,"max_positions":1,"enabled":True}}
        for key, value in defaults.items():
            db.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (key, json.dumps(value)))
            current=db.execute("SELECT value FROM settings WHERE key=?",(key,)).fetchone()
            if isinstance(value,dict) and current:
                merged={**value,**json.loads(current[0])}
                if key=="strategy":
                    for name in ("breakout_period","volume_period","vwap_period","min_relative_volume"):
                        merged.setdefault(name,value[name])
                    if merged.get("rsi_long")==50 and merged.get("rsi_short")==50:
                        merged["rsi_long"],merged["rsi_short"]=value["rsi_long"],value["rsi_short"]
                    # Keep the ATR stop consistent with the strategy's documented default.
                    if merged.get("atr_stop_multiplier")==2.0: merged["atr_stop_multiplier"]=value["atr_stop_multiplier"]
                db.execute("UPDATE settings SET value=? WHERE key=?",(json.dumps(merged),key))

def read_setting(key):
    with connect() as db:
        row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

def write_setting(key, value):
    with connect() as db:
        db.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))

def event(kind, detail):
    from datetime import datetime, timezone
    with connect() as db:
        db.execute("INSERT INTO events(at,kind,detail) VALUES(?,?,?)", (datetime.now(timezone.utc).isoformat(), kind, detail))
