"""
Usage:
    python seed.py              # create db + schema + sample data
    python seed.py --fresh      # delete existing hooter.db first
    python seed.py --hours 24   # generate 24h of heartbeat history (default 6)
    python seed.py --schema-only  # tables only, no sample data

"""

import argparse
import json
import random
import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "hooter.db"

HEARTBEAT_INTERVAL = 30  # seconds, matches firmware

SCHEMA = """
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
    uptime_sec     INTEGER
);

CREATE INDEX IF NOT EXISTS idx_hb_mac_ts ON heartbeats(mac, ts);
"""

FLEET = [
    ("Gate 1 Hooter",      "live1", -52, "healthy"),
    ("Gate 2 Hooter",      "live1", -58, "healthy"),
    ("Gate 3 Hooter",      "live1", -74, "weak_signal"),
    ("Substation Hooter",  "live1", -61, "brownout"),
    ("Workshop Hooter",    "live1", -55, "leaky_heap"),
    ("Yard North Hooter",  "live2", -66, "healthy"),
    ("Yard South Hooter",  "live2", -70, "no_stream"),
    ("Store Room Hooter",  "live2", -63, "healthy"),
    ("Canteen Hooter",     "live2", -80, "offline"),
    (None,                 None,    -59, "new_unit"),   # just powered on, unnamed
]


def make_mac(i: int) -> str:
    return f"A4:CF:12:9B:3E:{i:02X}"


def seed(hours: int, schema_only: bool) -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    conn.commit()

    if schema_only:
        print(f"Schema created in {DB_PATH} (no data).")
        conn.close()
        return

    now = int(time.time())
    span = hours * 3600
    total_hb = 0

    for i, (name, zone, base_rssi, profile) in enumerate(FLEET, start=1):
        mac = make_mac(i)
        ip = f"192.168.2.{60 + i}"
        fw = "1.1.0" if profile != "new_unit" else "1.0.0"

       
        if profile == "brownout":
            boot_ts = now - 45 * 60          # reset 45 min ago
            reset_reason = 9                 # brownout
        elif profile == "new_unit":
            boot_ts = now - 3 * 60           # powered on 3 min ago
            reset_reason = 1                 # power-on
        else:
            boot_ts = now - span - random.randint(0, 86400)
            reset_reason = 1

        
        last_hb_ts = now - 2 * 3600 if profile == "offline" else now

        first_hb_ts = max(now - span, boot_ts)
        underruns = 0
        wifi_reconnects = 0 if profile != "weak_signal" else random.randint(2, 5)
        heap_base = 165_000

        last_payload = None
        ts = first_hb_ts
        rows = []
        while ts <= last_hb_ts:
            rssi = base_rssi + random.randint(-4, 4)
            uptime_s = ts - boot_ts

            if profile == "weak_signal" and random.random() < 0.02:
                underruns += 1

            if profile == "leaky_heap":
              
                heap = heap_base - int((ts - first_hb_ts) / 3600 * 2048)
            else:
                heap = heap_base + random.randint(-3000, 3000)

            stream_connected = profile != "no_stream"
            buffer_level = (
                0 if not stream_connected
                else random.randint(6_000, 14_000) if profile == "weak_signal"
                else random.randint(24_000, 31_000)
            )

            rows.append((mac, ts, rssi, heap, buffer_level, underruns, uptime_s))

            last_payload = {
                "mac": mac, "ip": ip, "rssi": rssi,
                "uptime_s": uptime_s, "heap_free": heap,
                "stream_connected": stream_connected,
                "buffer_level": buffer_level, "underruns": underruns,
                "wifi_reconnects": wifi_reconnects,
                "reset_reason": reset_reason,
            }
            ts += HEARTBEAT_INTERVAL

        conn.executemany(
            """INSERT INTO heartbeats
               (mac, ts, rssi, heap_free, buffer_level, underruns, uptime_sec)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        conn.execute(
            """INSERT INTO devices
               (mac, name, zone, ip, first_seen, last_seen, last_payload)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(mac) DO UPDATE SET
                   name = excluded.name, zone = excluded.zone,
                   ip = excluded.ip,
                   last_seen = excluded.last_seen,
                   last_payload = excluded.last_payload""",
            (mac, name, zone, ip, first_hb_ts, last_hb_ts,
             json.dumps(last_payload)),
        )
        total_hb += len(rows)
        state = "OFFLINE" if profile == "offline" else "online"
        print(f"  {mac}  {name or '(unnamed)':<20} {profile:<12} "
              f"{len(rows):>5} heartbeats  [{state}]")

    conn.commit()
    conn.close()
    print(f"\nSeeded {len(FLEET)} devices, {total_hb} heartbeats "
          f"({hours}h history) -> {DB_PATH}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Create and seed fleet.db")
    ap.add_argument("--fresh", action="store_true",
                    help="delete existing fleet.db first")
    ap.add_argument("--hours", type=int, default=6,
                    help="hours of heartbeat history to generate (default 6)")
    ap.add_argument("--schema-only", action="store_true",
                    help="create tables only, no sample data")
    args = ap.parse_args()

    if args.fresh:
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(DB_PATH) + suffix)
            if p.exists():
                p.unlink()
                print(f"Deleted {p.name}")

    seed(args.hours, args.schema_only)