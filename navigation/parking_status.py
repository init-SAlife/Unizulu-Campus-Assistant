# which parking areas are FULL right now.

import os
import sqlite3
import threading
import time
from pathlib import Path

DB_PATH = os.environ.get("PARKING_DB") or str(Path(__file__).resolve().parent / "parking_status.db")
try:
    FULL_MINUTES = float(os.environ.get("PARKING_FULL_MINUTES", 45))
except ValueError:
    FULL_MINUTES = 45.0
REPORT_GAP_S = 3          # the same browser cannot flip the same parking more often than this

_init_lock = threading.Lock()
_ready = False
_last_report = {}         # (client, parking id) -> time, simple flood protection
_last_lock = threading.Lock()


def _postgres():
    # True inside the Campus AI Assistant: accounts.py has a database pool ready.
    try:
        from services import accounts
    except Exception:
        return False
    return getattr(accounts, "_pool_settings", None) is not None


def _query(sql, params=(), write=False):
    # Run one statement (written with %s placeholders) on PostgreSQL or SQLite.
    if _postgres():
        from services import accounts
        with accounts._cursor() as cur:
            cur.execute(sql, params)
            return [] if write else cur.fetchall()
    con = _connect()
    try:
        rows = con.execute(sql.replace("%s", "?"), params).fetchall()
        if write:
            con.commit()
        return rows
    finally:
        con.close()


def _connect():
    global _ready
    con = sqlite3.connect(DB_PATH, timeout=5)
    if not _ready:
        with _init_lock:
            if not _ready:
                con.execute("PRAGMA journal_mode=WAL")      # many readers + one writer at a time
                _ready = True
    # cheap, and it heals a database file that was deleted or reset while the server runs
    con.execute("""CREATE TABLE IF NOT EXISTS parking_status (
                       parking_id  TEXT PRIMARY KEY,
                       status      TEXT NOT NULL,      -- 'full' | 'available'
                       reported_at REAL NOT NULL,      -- unix time
                       reports     INTEGER NOT NULL DEFAULT 1)""")
    return con


def full_ids():
    # The ids of the parkings that are full right now (reports not yet expired).
    since = time.time() - FULL_MINUTES * 60
    try:
        rows = _query("SELECT parking_id FROM parking_status WHERE status='full' AND reported_at>=%s", (since,))
    except Exception as exc:
        print(f"[parking] status could not be read ({exc}); treating every parking as free")
        return frozenset()
    return frozenset(r[0] for r in rows)


def snapshot(parkings):
    # Status of every parking: [
    now = time.time()
    rows = {}
    try:
        rows = {r[0]: r for r in _query("SELECT parking_id, status, reported_at, reports FROM parking_status")}
    except Exception as exc:
        print(f"[parking] status could not be read ({exc})")
    out = []
    for P in parkings:
        row = rows.get(P["id"])
        full = bool(row and row[1] == "full" and now - row[2] <= FULL_MINUTES * 60)
        item = {"id": P["id"], "name": P["name"], "lat": round(P["lat"], 7), "lng": round(P["lng"], 7),
                "capacity": P.get("capacity"), "full": full, "reported_min_ago": None, "expires_in_min": None}
        if row and (full or row[1] == "available"):
            item["reported_min_ago"] = round((now - row[2]) / 60, 1)
        if full:
            item["expires_in_min"] = round(FULL_MINUTES - (now - row[2]) / 60, 1)
        out.append(item)
    return out


class TooFast(Exception):
    # The same client reported the same parking a moment ago.
    pass


def report(parking_id, status, client="?"):
    # Record a 'full' or 'available' report for one parking.
    if status not in ("full", "available"):
        raise ValueError("status must be 'full' or 'available'")
    now = time.time()
    with _last_lock:
        key = (client, parking_id, status)      # undoing a report straight away is allowed
        if now - _last_report.get(key, 0) < REPORT_GAP_S:
            raise TooFast()
        _last_report[key] = now
        if len(_last_report) > 5000:                     # keep the memory small
            for k in sorted(_last_report, key=_last_report.get)[:2500]:
                _last_report.pop(k, None)
    # same statement for PostgreSQL and SQLite (both understand ON CONFLICT ... excluded)
    _query("""INSERT INTO parking_status (parking_id, status, reported_at, reports)
              VALUES (%s, %s, %s, 1)
              ON CONFLICT (parking_id) DO UPDATE SET
                  reports = CASE WHEN parking_status.status = excluded.status
                                 THEN parking_status.reports + 1 ELSE 1 END,
                  status = excluded.status,
                  reported_at = excluded.reported_at""",
           (parking_id, status, now), write=True)
