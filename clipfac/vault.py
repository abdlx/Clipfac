"""Encrypted local provider settings. Tokens are never returned to the browser."""
import os
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from cryptography.fernet import Fernet
from fastapi import APIRouter, HTTPException
from pydantic import Field, model_validator
from starlette.middleware.base import BaseHTTPMiddleware

from clipfac import store
from clipfac.models import StrictModel

router = APIRouter(prefix="/api/clipfac/providers", tags=["Provider settings"])
_LOCK = threading.RLock()
MAPPING = {
    "gemini": {"token": "GEMINI_API_KEY", "model": "GEMINI_MODEL", "endpoint": "GOOGLE_GEMINI_BASE_URL"},
    "text": {"token": "LLM_API_KEY", "endpoint": "LLM_BASE_URL", "model": "LLM_MODEL"},
    "vision": {"token": "CLIPFAC_VISION_API_KEY", "endpoint": "CLIPFAC_VISION_BASE_URL", "model": "CLIPFAC_VISION_MODEL"},
    "upload_post": {"token": "UPLOAD_POST_API_KEY", "model": "CLIPFAC_UPLOAD_PROFILE"},
    "elevenlabs": {"token": "ELEVENLABS_API_KEY"},
    "fal": {"token": "FAL_KEY"},
    # Not an LLM provider: the operator's YouTube session cookies, stored as the
    # token blob. apply_runtime() exposes them as YOUTUBE_COOKIES, which the
    # downloader already reads per job and writes to /app/cookies.txt for yt-dlp.
    "youtube": {"token": "YOUTUBE_COOKIES"},
}
ENV_KEYS = {value for values in MAPPING.values() for value in values.values()} | {"LLM_PROVIDER"}
_BASE_ENV = {key: os.environ.get(key) for key in ENV_KEYS}


class ProviderUpdate(StrictModel):
    enabled: bool = True
    endpoint: str | None = Field(default=None, max_length=4096)
    model: str | None = Field(default=None, max_length=200)
    # 64K: a full YouTube cookies.txt export can exceed the old 8K cap.
    token: str | None = Field(default=None, max_length=65536)
    profile: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def url_without_credentials(self):
        if self.endpoint:
            url = urlparse(self.endpoint)
            if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.query or url.fragment:
                raise ValueError("Endpoint needs an HTTP(S) URL without credentials, query or fragment")
        return self


def setup(db):
    db.execute("""CREATE TABLE IF NOT EXISTS provider_settings (
        kind TEXT PRIMARY KEY, enabled INTEGER NOT NULL, endpoint TEXT NOT NULL,
        model TEXT NOT NULL, ciphertext TEXT, updated REAL NOT NULL)""")
    db.commit()


def cipher():
    override = os.environ.get("CLIPFAC_VAULT_KEY")
    if override:
        return Fernet(override.encode())
    # Use a persisted, private key file. Database backups alone cannot decrypt it.
    # The lock makes first startup safe if two server instances share the volume.
    import fcntl
    directory = Path(os.environ.get("CLIPFAC_DB_PATH", "data/clipfac.sqlite3")).parent
    directory.mkdir(parents=True, exist_ok=True)
    lock = directory / ".clipfac-vault.lock"
    key_path = directory / "clipfac-vault.key"
    with open(lock, "a+b") as handle:
        os.chmod(lock, 0o600)
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            if not key_path.exists():
                fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as key_file:
                    key_file.write(Fernet.generate_key())
                    key_file.flush()
                    os.fsync(key_file.fileno())
            else:
                os.chmod(key_path, 0o600)
            return Fernet(key_path.read_bytes())
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def rows():
    with store.connect() as db:
        setup(db)
        return [dict(row) for row in db.execute("SELECT * FROM provider_settings")]


def public_settings():
    stored = {row["kind"]: row for row in rows()}
    result = []
    for kind, mapping in MAPPING.items():
        row = stored.get(kind)
        result.append({
            "kind": kind, "enabled": bool(row["enabled"]) if row else True,
            "endpoint": row["endpoint"] if row else os.environ.get(mapping.get("endpoint", ""), ""),
            "model": row["model"] if row else os.environ.get(mapping.get("model", ""), ""),
            "token_configured": bool(row["ciphertext"]) if row else bool(os.environ.get(mapping["token"])),
            "source": "vault" if row else "environment", "updated": row["updated"] if row else None,
        })
    return result


def _validate_youtube_cookies(blob):
    """A cookies blob must parse as a Netscape cookie file with a youtube.com cookie.

    Rejects garbage pastes before they are encrypted, so a bad save can never
    silently break every YouTube download. Accepts exports with or without the
    '# Netscape HTTP Cookie File' header line.
    """
    import tempfile
    from http.cookiejar import MozillaCookieJar
    text = blob.strip()
    if not text:
        raise ValueError("YouTube cookies are empty")
    if not text.startswith("# Netscape"):
        text = "# Netscape HTTP Cookie File\n" + text
    # Pre-check line shape first: handed a blob that is not a cookie file, the
    # stdlib prints a noisy "http.cookiejar bug!" traceback before raising.
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if len(line.split("\t")) < 7:
            raise ValueError("That does not look like a Netscape cookies.txt export")
    jar = MozillaCookieJar()
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
        handle.write(text)
        path = handle.name
    try:
        jar.load(path, ignore_discard=True, ignore_expires=True)
    except Exception as exc:
        raise ValueError("That does not look like a Netscape cookies.txt export") from exc
    finally:
        os.unlink(path)
    if not any("youtube.com" in cookie.domain for cookie in jar):
        raise ValueError("No youtube.com cookies in the export — export cookies for youtube.com")
    return True


def save(kind, update):
    if kind not in MAPPING:
        raise ValueError("Unknown provider kind")
    if kind == "youtube" and update.token:
        _validate_youtube_cookies(update.token)
    with _LOCK, store.connect() as db:
        setup(db)
        previous = db.execute("SELECT * FROM provider_settings WHERE kind=?", (kind,)).fetchone()
        endpoint = update.endpoint if update.endpoint is not None else (previous["endpoint"] if previous else "")
        selected_model = update.profile if kind == "upload_post" and update.profile is not None else update.model
        model = selected_model if selected_model is not None else (previous["model"] if previous else "")
        ciphertext = previous["ciphertext"] if previous else None
        if previous is None and update.token is None and os.environ.get(MAPPING[kind]["token"]):
            ciphertext = cipher().encrypt(os.environ[MAPPING[kind]["token"]].encode()).decode()
        if update.token is not None:
            ciphertext = cipher().encrypt(update.token.encode()).decode() if update.token else None
        if update.enabled and kind in ("text", "vision") and (not endpoint or not model):
            raise ValueError("A compatible provider needs an endpoint and model ID")
        db.execute("INSERT OR REPLACE INTO provider_settings VALUES (?,?,?,?,?,?)",
                   (kind, int(update.enabled), endpoint.rstrip("/"), model.strip(), ciphertext, time.time()))
        db.commit()
    apply_runtime()
    return next(item for item in public_settings() if item["kind"] == kind)


def delete(kind):
    if kind not in MAPPING:
        raise ValueError("Unknown provider kind")
    with _LOCK, store.connect() as db:
        setup(db)
        db.execute("DELETE FROM provider_settings WHERE kind=?", (kind,))
        db.commit()
    apply_runtime()


def apply_runtime():
    """Future jobs get a consistent snapshot; running job subprocesses retain theirs."""
    with _LOCK:
        # Start with deployment environment so removing an override restores it.
        for key, value in _BASE_ENV.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        for row in rows():
            mapping = MAPPING.get(row["kind"])
            if not mapping:
                continue
            if not row["enabled"]:
                for variable in mapping.values():
                    os.environ.pop(variable, None)
                if row["kind"] == "text":
                    os.environ.pop("LLM_PROVIDER", None)
                continue
            for field in ("endpoint", "model"):
                if mapping.get(field) and row[field]:
                    os.environ[mapping[field]] = row[field]
            if row["ciphertext"]:
                os.environ[mapping["token"]] = cipher().decrypt(row["ciphertext"].encode()).decode()
            else:
                os.environ.pop(mapping["token"], None)
            if row["kind"] == "text":
                os.environ["LLM_PROVIDER"] = "openai"


class ProviderHeaders(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        # Provider credentials are injected server-side, never sent to browser JS.
        if request.url.path.startswith("/api/"):
            defaults = {
                b"x-gemini-key": "GEMINI_API_KEY", b"x-upload-post-key": "UPLOAD_POST_API_KEY",
                b"x-elevenlabs-key": "ELEVENLABS_API_KEY", b"x-fal-key": "FAL_KEY",
            }
            headers = list(request.scope["headers"])
            existing = {key.lower() for key, _ in headers}
            with _LOCK:
                for header, variable in defaults.items():
                    token = os.environ.get(variable)
                    if token and header not in existing:
                        headers.append((header, token.encode()))
            request.scope["headers"] = headers
        return await call_next(request)


@router.get("")
async def provider_settings():
    return {"providers": public_settings(), "notice": "Configured does not mean provider-tested. Tokens are encrypted at rest and never returned."}


@router.put("/{kind}")
async def update_provider(kind: str, body: ProviderUpdate):
    try:
        return save(kind, body)
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@router.delete("/{kind}")
async def delete_provider(kind: str):
    try:
        delete(kind)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return {"kind": kind, "source": "environment"}
