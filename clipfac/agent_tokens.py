"""Agent tokens: named, scoped credentials for MCP clients and other agents.

The installation admin token is the master key — it must never be handed to
an agent. An agent token is a narrow credential instead: a name ("hob Main",
"Claude Code"), a scope subset (read/process/edit/publish — admin is never
grantable), and a value that is shown exactly once at creation.

Storage: only the SHA-256 hash of the value is kept (same as a password
hash). The plaintext value exists only in the create response and is never
written to disk, logs or the ledger. Verification hashes the presented
token and looks the hash up, so a stolen database still does not yield a
usable token.

Env-var tokens (CLIPFAC_AGENT_TOKENS) keep working unchanged — they are
the escape hatch for automation that provisions the install itself. Tokens
created here live in the same SQLite database as the productions ledger, so
they survive redeploys and need no Coolify env edit.
"""
import hashlib
import json
import secrets
import time
import uuid

from clipfac import store

PREFIX = "cft_"
# Admin scope is deliberately absent: no agent token can manage tokens,
# providers or the installation itself.
SCOPES = {"read", "process", "edit", "publish"}


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def create_token(name, scopes):
    """Mint a token. Returns the record plus the plaintext value, once.

    Raises ValueError on a bad name or an unknown/empty scope set.
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("Give the token a name, e.g. the agent it belongs to")
    if len(name) > 80:
        raise ValueError("Token name is too long (max 80 characters)")
    scopes = set(scopes or [])
    if not scopes:
        raise ValueError("Pick at least one scope")
    if not scopes <= SCOPES:
        raise ValueError(f"Unknown scope: {sorted(scopes - SCOPES)}")

    token = PREFIX + secrets.token_urlsafe(24)
    record = {
        "id": uuid.uuid4().hex[:12],
        "name": name,
        "token_hash": _hash(token),
        # First 8 chars of the secret part, for recognising the token later.
        "token_prefix": token[: len(PREFIX) + 8],
        "scopes": json.dumps(sorted(scopes)),
        "created": time.time(),
        "last_used": None,
        "revoked": None,
    }
    with store.connect() as db:
        db.execute(
            """CREATE TABLE IF NOT EXISTS agent_tokens (
                id TEXT PRIMARY KEY, name TEXT NOT NULL,
                token_hash TEXT NOT NULL UNIQUE, token_prefix TEXT NOT NULL,
                scopes TEXT NOT NULL, created REAL NOT NULL,
                last_used REAL, revoked REAL)"""
        )
        db.execute(
            "INSERT INTO agent_tokens (id, name, token_hash, token_prefix,"
            " scopes, created, last_used, revoked) VALUES"
            " (:id, :name, :token_hash, :token_prefix, :scopes, :created,"
            " :last_used, :revoked)",
            record,
        )
        db.commit()
    return {
        "id": record["id"],
        "name": name,
        "scopes": sorted(scopes),
        "token": token,
        "prefix": record["token_prefix"],
        "created": record["created"],
    }


def _public(row):
    return {
        "id": row["id"],
        "name": row["name"],
        "prefix": row["token_prefix"],
        "scopes": json.loads(row["scopes"]),
        "created": row["created"],
        "last_used": row["last_used"],
        "revoked": row["revoked"],
    }


def list_tokens(include_revoked=False):
    """All tokens, newest first. Never includes a hash or a plaintext value."""
    with store.connect() as db:
        db.execute(
            """CREATE TABLE IF NOT EXISTS agent_tokens (
                id TEXT PRIMARY KEY, name TEXT NOT NULL,
                token_hash TEXT NOT NULL UNIQUE, token_prefix TEXT NOT NULL,
                scopes TEXT NOT NULL, created REAL NOT NULL,
                last_used REAL, revoked REAL)"""
        )
        query = "SELECT * FROM agent_tokens"
        if not include_revoked:
            query += " WHERE revoked IS NULL"
        query += " ORDER BY created DESC"
        return [_public(row) for row in db.execute(query).fetchall()]


def revoke_token(token_id):
    """Revoke a token. Returns True if a live token was revoked."""
    with store.connect() as db:
        cursor = db.execute(
            "UPDATE agent_tokens SET revoked = ?"
            " WHERE id = ? AND revoked IS NULL",
            (time.time(), token_id),
        )
        db.commit()
        return cursor.rowcount == 1


def verify_token(token):
    """Check a presented token. Returns {"owner", "scopes"} or None.

    A match updates last_used (best effort — a failed write must not
    reject an otherwise valid token).
    """
    if not token or not token.startswith(PREFIX):
        return None
    with store.connect() as db:
        db.execute(
            """CREATE TABLE IF NOT EXISTS agent_tokens (
                id TEXT PRIMARY KEY, name TEXT NOT NULL,
                token_hash TEXT NOT NULL UNIQUE, token_prefix TEXT NOT NULL,
                scopes TEXT NOT NULL, created REAL NOT NULL,
                last_used REAL, revoked REAL)"""
        )
        row = db.execute(
            "SELECT * FROM agent_tokens WHERE token_hash = ? AND revoked IS NULL",
            (_hash(token),),
        ).fetchone()
        if not row:
            return None
        try:
            db.execute(
                "UPDATE agent_tokens SET last_used = ? WHERE id = ?",
                (time.time(), row["id"]),
            )
            db.commit()
        except Exception:
            pass
        return {"owner": f"agent:{row['id']}", "scopes": set(json.loads(row["scopes"]))}
