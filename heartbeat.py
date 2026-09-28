"""
heartbeat.py - Hooter heartbeat ingestion + monitoring dashboard.
Mounted onto the main server as an APIRouter.


Endpoints:
    POST  /api/heartbeat          <- ESP32 units post here every 30s
    GET   /api/devices            <- live fleet JSON (live calculated)
    PATCH /api/devices/{mac}      <- assign name
    GET   /api/devices/{mac}/history?hours=24
    GET   /dashboard              <- Monitoring page
"""

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "hooter.db"
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

OFFLINE_AFTER_S = 75            # no heartbeat for this long -> offline
RETENTION_DAYS = 30
CLEANUP_EVERY_N_HEARTBEATS = 1000

router = APIRouter()

_conn: Optional[sqlite3.Connection] = None
_lock = threading.Lock()
_hb_counter = 0

# esp_reset_reason() codes -> human label
RESET_REASONS = {
    0: "unknown", 1: "power-on", 2: "external", 3: "software",
    4: "panic", 5: "int. watchdog", 6: "task watchdog", 7: "watchdog",
    8: "deep sleep", 9: "brownout", 10: "SDIO",
}


# ---------------------------------------------------------------- database

def init_hooter_db() -> None:
    """Open the DB and ensure schema exists. Call once in lifespan()."""
    global _conn
    _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    # _conn = sqlite3.connect(DB_PATH)
    _conn.row_factory = sqlite3.Row
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.execute("PRAGMA synchronous=NORMAL")
    _conn.executescript("""
        CREATE TABLE IF NOT EXISTS devices (
            mac          TEXT PRIMARY KEY,
            name         TEXT,
            zone         TEXT,
            ip           TEXT,
            first_seen   INTEGER NOT NULL,
            last_seen    INTEGER NOT NULL,
            last_payload TEXT
        );
        CREATE TABLE IF NOT EXISTS heartbeats (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            mac          TEXT NOT NULL,
            ts           INTEGER NOT NULL,
            rssi         INTEGER,
            heap_free    INTEGER,
            buffer_level INTEGER,
            underruns    INTEGER,
            uptime_s     INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_hb_mac_ts ON heartbeats(mac, ts);
    """)
    _conn.commit()
    log.info("Hooter DB ready at %s", DB_PATH)


def _db() -> sqlite3.Connection:
    if _conn is None:
        raise RuntimeError("init_hooter_db() was not called in lifespan()")
    return _conn


def _cleanup_old_heartbeats() -> None:
    cutoff = int(time.time()) - RETENTION_DAYS * 86400
    with _lock:
        cur = _db().execute("DELETE FROM heartbeats WHERE ts < ?", (cutoff,))
        _db().commit()
    if cur.rowcount:
        log.info("Heartbeat retention: pruned %d rows", cur.rowcount)


def get_devices() -> list[dict]:
    """Live hooter view with online status computed from last_seen."""
    now = int(time.time())
    with _lock:
        rows = _db().execute(
            "SELECT * FROM devices ORDER BY name IS NULL, name, mac"
        ).fetchall()

    out = []
    for r in rows:
        p = json.loads(r["last_payload"] or "{}")
        age = now - r["last_seen"]
        out.append({
            "mac": r["mac"],
            "name": r["name"],
            "zone": r["zone"],
            "ip": r["ip"],
            "online": age < OFFLINE_AFTER_S,
            "last_seen_s_ago": age,
            "rssi": p.get("rssi"),
            "uptime_s": p.get("uptime_sec"),
            "heap_free": p.get("heap_free"),
            "stream_connected": p.get("stream_connected"),
            "buffer_level": p.get("buffer_level"),
            "underruns": p.get("underruns"),
            "wifi_reconnects": p.get("wifi_reconnects"),
            "reset_reason": RESET_REASONS.get(p.get("reset_reason"),
                                              p.get("reset_reason")),
            "vbat": p.get("vbat"),
        })
    return out


# Models
class Heartbeat(BaseModel):
    mac: str = Field(min_length=11, max_length=17)
    ip: Optional[str] = None
    rssi: Optional[int] = None
    uptime_sec: Optional[int] = None
    heap_free: Optional[int] = None
    stream_connected: Optional[bool] = None
    buffer_level: Optional[int] = None
    underruns: Optional[int] = None
    wifi_reconnects: Optional[int] = None
    reset_reason: Optional[int] = None
    vbat: Optional[float] = None


class DevicePatch(BaseModel):
    name: Optional[str] = None
    zone: Optional[str] = None


# Endpoints
@router.post("/api/heartbeat")
def heartbeat(hb: Heartbeat):
    global _hb_counter
    now = int(time.time())
    mac = hb.mac.upper()
    payload = hb.model_dump()

    with _lock:
        db = _db()
        db.execute(
            """
            INSERT INTO devices (mac, ip, first_seen, last_seen, last_payload)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(mac) DO UPDATE SET
                ip = excluded.ip,
                last_seen = excluded.last_seen,
                last_payload = excluded.last_payload
            """,
            (mac, hb.ip, now, now, json.dumps(payload)),
        )
        db.execute(
            """
            INSERT INTO heartbeats
                (mac, ts, rssi, heap_free, buffer_level, underruns, uptime_sec)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (mac, now, hb.rssi, hb.heap_free, hb.buffer_level,
             hb.underruns, hb.uptime_sec),
        )
        db.commit()

    _hb_counter += 1
    if _hb_counter % CLEANUP_EVERY_N_HEARTBEATS == 0:
        _cleanup_old_heartbeats()

    return {"status": "ok"}


@router.get("/api/devices")
def api_devices():
    return get_devices()


@router.patch("/api/devices/{mac}")
def update_device(mac: str, patch: DevicePatch):
    mac = mac.upper()
    fields, values = [], []
    if patch.name is not None:
        fields.append("name = ?")
        values.append(patch.name.strip() or None)
    if patch.zone is not None:
        fields.append("zone = ?")
        values.append(patch.zone.strip() or None)
    if not fields:
        raise HTTPException(400, "Nothing to update")

    with _lock:
        cur = _db().execute(
            f"UPDATE devices SET {', '.join(fields)} WHERE mac = ?",
            (*values, mac),
        )
        _db().commit()
    if cur.rowcount == 0:
        raise HTTPException(404, "Unknown device")
    return {"status": "ok"}


@router.get("/api/devices/{mac}/history")
def device_history(mac: str, hours: int = 24):
    since = int(time.time()) - hours * 3600
    with _lock:
        rows = _db().execute(
            """
            SELECT ts, rssi, heap_free, buffer_level, underruns, uptime_sec
            FROM heartbeats WHERE mac = ? AND ts >= ? ORDER BY ts
            """,
            (mac.upper(), since),
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    devices = get_devices()
    online = sum(1 for d in devices if d["online"])
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "devices": devices,
            "online": online,
            "total": len(devices),
        },
    )