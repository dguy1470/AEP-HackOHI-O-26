"""
SQLite persistence for the Outage Concierge backend.

SQLite is built into Python (no server to run) and is plenty for a prototype.
Two tables:
  - subscribers: who wants alerts, on which channels, for which area
  - sent_log:    a record of every message we attempted to send

The DB file lives next to this module (concierge.db) and is gitignored.
Swap the connection for Postgres later without changing the call sites.
"""
import os
import sqlite3
import datetime
from contextlib import contextmanager

DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "concierge.db"))


@contextmanager
def _conn():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_db():
    with _conn() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS subscribers (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                email     TEXT,
                phone     TEXT,
                channels  TEXT NOT NULL,          -- comma-separated: email,sms
                area      TEXT,
                active    INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sent_log (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                channel   TEXT NOT NULL,          -- email or sms
                target    TEXT NOT NULL,          -- address or phone
                subject   TEXT,
                body      TEXT,
                status    TEXT NOT NULL,          -- sent, demo, error
                detail    TEXT,
                created_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS ux_sub_email ON subscribers(email) WHERE email IS NOT NULL;
            """
        )


def _now():
    return datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"


def upsert_subscriber(email, phone, channels, area):
    """Insert a subscriber, or update channels/phone/area if the email already exists."""
    ch = ",".join(channels)
    with _conn() as con:
        if email:
            row = con.execute("SELECT id FROM subscribers WHERE email=?", (email,)).fetchone()
            if row:
                con.execute(
                    "UPDATE subscribers SET phone=?, channels=?, area=?, active=1 WHERE id=?",
                    (phone, ch, area, row["id"]),
                )
                return row["id"]
        cur = con.execute(
            "INSERT INTO subscribers(email, phone, channels, area, active, created_at) VALUES (?,?,?,?,1,?)",
            (email, phone, ch, area, _now()),
        )
        return cur.lastrowid


def list_subscribers(active_only=True):
    q = "SELECT * FROM subscribers" + (" WHERE active=1" if active_only else "") + " ORDER BY id DESC"
    with _conn() as con:
        return [dict(r) for r in con.execute(q).fetchall()]


def deactivate(email=None, phone=None):
    with _conn() as con:
        if email:
            con.execute("UPDATE subscribers SET active=0 WHERE email=?", (email,))
        if phone:
            con.execute("UPDATE subscribers SET active=0 WHERE phone=?", (phone,))


def log_send(channel, target, subject, body, status, detail=""):
    with _conn() as con:
        con.execute(
            "INSERT INTO sent_log(channel, target, subject, body, status, detail, created_at) VALUES (?,?,?,?,?,?,?)",
            (channel, target, subject, body, status, detail, _now()),
        )


def recent_log(limit=50):
    with _conn() as con:
        return [dict(r) for r in con.execute(
            "SELECT * FROM sent_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()]
