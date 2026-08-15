"""SQLite access layer."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .config import settings

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or settings.db_path
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Path | None = None) -> None:
    conn = connect(db_path)
    with conn:
        conn.executescript(SCHEMA_PATH.read_text())
    conn.close()


def start_run(conn: sqlite3.Connection, kind: str) -> int:
    cur = conn.execute(
        "INSERT INTO runs(kind, started_at, status) VALUES (?,?,?)",
        (kind, now_iso(), "running"),
    )
    conn.commit()
    return cur.lastrowid


def finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    rows: int,
    api_calls: int,
    status: str,
    note: str = "",
) -> None:
    conn.execute(
        "UPDATE runs SET finished_at=?, rows_written=?, api_calls=?, status=?, note=? WHERE id=?",
        (now_iso(), rows, api_calls, status, note, run_id),
    )
    conn.commit()


STATE_BY_SA2_PREFIX = {
    "1": "NSW", "2": "VIC", "3": "QLD", "4": "SA",
    "5": "WA", "6": "TAS", "7": "NT", "8": "ACT", "9": "OT",
}


def state_from_sa2(sa2_code: str) -> str:
    return STATE_BY_SA2_PREFIX.get(sa2_code[:1], "??")
