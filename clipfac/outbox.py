"""Persistent, leased, at-least-once webhook delivery with stable event IDs."""
import asyncio
import hashlib
import hmac
import json
import os
import time
import uuid
from pathlib import Path

import httpx
from clipfac import store

BACKOFF = (10, 60, 300, 1800, 7200, 21600)
MAX_ATTEMPTS = 8


def setup(db):
    db.execute("""CREATE TABLE IF NOT EXISTS webhook_events (
        id TEXT PRIMARY KEY, job_id TEXT NOT NULL, kind TEXT NOT NULL, url TEXT NOT NULL,
        secret TEXT, body TEXT NOT NULL, state TEXT NOT NULL, attempts INTEGER NOT NULL,
        due REAL NOT NULL, lease TEXT, lease_until REAL, last_error TEXT,
        created REAL NOT NULL, delivered REAL, UNIQUE(job_id,kind,url))""")
    db.execute("CREATE TABLE IF NOT EXISTS webhook_terminal_jobs (job_id TEXT PRIMARY KEY, snapshot TEXT NOT NULL)")
    db.commit()


def enqueue(job_id, url, secret, payload, snapshot):
    now = time.time()
    identity = str(uuid.uuid4())
    body = {**payload, "delivery_id": identity, "created_at": now}
    with store.connect() as db:
        setup(db)
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute("SELECT id FROM webhook_events WHERE job_id=? AND kind=? AND url=?",
                              (job_id, payload["event"], url)).fetchone()
        if existing:
            return existing[0]
        db.execute("INSERT OR REPLACE INTO webhook_terminal_jobs VALUES (?,?)", (job_id, json.dumps(snapshot)))
        db.execute("INSERT INTO webhook_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (identity, job_id, payload["event"], url, secret, json.dumps(body, separators=(",", ":")),
                    "pending", 0, now, None, None, None, now, None))
        db.commit()
    return identity


def terminal(job_id):
    # Recovery scans should not create a database on installations without callbacks.
    path = Path(os.environ.get("CLIPFAC_DB_PATH", "data/clipfac.sqlite3"))
    if not path.exists():
        return None
    with store.connect() as db:
        try:
            row = db.execute("SELECT snapshot FROM webhook_terminal_jobs WHERE job_id=?", (job_id,)).fetchone()
            return json.loads(row[0]) if row else None
        except Exception:
            return None


def claim(now=None):
    now = time.time() if now is None else now
    with store.connect() as db:
        setup(db)
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("""SELECT * FROM webhook_events
            WHERE (state='pending' AND due<=?) OR (state='sending' AND lease_until<=?)
            ORDER BY due LIMIT 1""", (now, now)).fetchone()
        if not row:
            return None
        lease = str(uuid.uuid4())
        db.execute("UPDATE webhook_events SET state='sending', attempts=attempts+1, lease=?, lease_until=? WHERE id=?",
                   (lease, now + 90, row["id"]))
        db.commit()
        return {**dict(row), "lease": lease, "attempts": row["attempts"] + 1}


def settle(event, success, error=None, permanent=False, now=None):
    now = time.time() if now is None else now
    attempts = event["attempts"]
    state = "delivered" if success else ("dead_letter" if permanent or attempts >= MAX_ATTEMPTS else "pending")
    due = now + BACKOFF[min(attempts - 1, len(BACKOFF) - 1)]
    with store.connect() as db:
        setup(db)
        db.execute("""UPDATE webhook_events SET state=?, due=?, last_error=?,
            delivered=?, lease=NULL, lease_until=NULL WHERE id=? AND lease=?""",
            (state, due, error, now if success else None, event["id"], event["lease"]))
        db.commit()


def send_once(event):
    import ssrf_guard
    from security_utils import assert_public_url, UnsafeURLError
    body = event["body"].encode()
    timestamp = str(int(time.time()))
    headers = {"Content-Type": "application/json", "User-Agent": "Clipfac-Webhook/1.0",
               "X-Clipfac-Delivery-ID": event["id"], "X-Clipfac-Timestamp": timestamp}
    if event["secret"]:
        secret = event["secret"].encode()
        headers["X-OpenShorts-Signature"] = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
        headers["X-Clipfac-Signature"] = "v1=" + hmac.new(secret, timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    try:
        # DNS and redirect targets are checked at connect time as well as enqueue.
        assert_public_url(event["url"])
        with ssrf_guard.guarded(), httpx.Client(timeout=15, follow_redirects=False) as client:
            response = client.post(event["url"], content=body, headers=headers)
        if 200 <= response.status_code < 300:
            return True, None, False
        permanent = 300 <= response.status_code < 500 and response.status_code not in (408, 425, 429)
        return False, f"HTTP {response.status_code}", permanent
    except UnsafeURLError:
        return False, "Unsafe webhook target", True
    except Exception as exc:
        return False, type(exc).__name__, False


async def run():
    while True:
        event = None
        try:
            event = claim()
            if event:
                success, error, permanent = await asyncio.to_thread(send_once, event)
                settle(event, success, error, permanent)
                continue
        except asyncio.CancelledError:
            raise  # Unacknowledged lease becomes reclaimable after its expiry.
        except Exception as exc:
            print(f"[clipfac-outbox] {type(exc).__name__}", flush=True)
        await asyncio.sleep(2)


def deliveries(job_id):
    with store.connect() as db:
        setup(db)
        rows = db.execute("""SELECT id,job_id,kind,state,attempts,due,last_error,created,delivered
            FROM webhook_events WHERE job_id=? ORDER BY created DESC LIMIT 50""", (job_id,))
        return [dict(row) for row in rows]


def retry(delivery_id):
    with store.connect() as db:
        setup(db)
        changed = db.execute("""UPDATE webhook_events SET state='pending',attempts=0,due=?,
            lease=NULL,lease_until=NULL,last_error=NULL WHERE id=? AND state='dead_letter'""",
            (time.time(), delivery_id)).rowcount
        db.commit()
        return bool(changed)
