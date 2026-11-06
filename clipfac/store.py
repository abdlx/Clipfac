"""SQLite submission ledger. An uncertain submission is never silently replayed."""
import hashlib
import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


class Conflict(ValueError):
    pass


@contextmanager
def connect():
    path = Path(os.environ.get("CLIPFAC_DB_PATH", "data/clipfac.sqlite3"))
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=20)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("""CREATE TABLE IF NOT EXISTS productions (
            id TEXT PRIMARY KEY, owner TEXT NOT NULL, key TEXT NOT NULL,
            digest TEXT NOT NULL, request TEXT NOT NULL, brief TEXT NOT NULL,
            state TEXT NOT NULL, response TEXT, created REAL NOT NULL,
            UNIQUE(owner, key))""")
        db.commit()
        yield db
    finally:
        db.close()


def reserve(owner, key, request, brief):
    encoded = json.dumps(request, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM productions WHERE owner=? AND key=?", (owner, key)).fetchone()
        if row:
            if row["digest"] != digest:
                raise Conflict("Idempotency key already belongs to a different request")
            return decode(row), False
        identity = str(uuid.uuid4())
        db.execute("INSERT INTO productions VALUES (?,?,?,?,?,?,?,?,?)",
                   (identity, owner, key, digest, encoded, json.dumps(brief), "submitting", None, time.time()))
        db.commit()
        return get(identity, owner), True


def decode(row):
    if row is None:
        return None
    data = dict(row)
    for field in ("request", "brief", "response"):
        data[field] = json.loads(data[field]) if data[field] else None
    # Idempotency keys are credentials-adjacent operational data; don't emit them.
    data.pop("key", None)
    data.pop("digest", None)
    data.pop("owner", None)
    return data


def get(identity, owner):
    with connect() as db:
        return decode(db.execute("SELECT * FROM productions WHERE id=? AND owner=?", (identity, owner)).fetchone())


def finish(identity, state, response):
    with connect() as db:
        db.execute("UPDATE productions SET state=?, response=? WHERE id=?",
                   (state, json.dumps(response), identity))
        db.commit()


def recent(owner, limit=30):
    with connect() as db:
        return [decode(row) for row in db.execute(
            "SELECT * FROM productions WHERE owner=? ORDER BY created DESC LIMIT ?", (owner, limit))]
