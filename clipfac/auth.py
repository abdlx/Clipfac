"""Single-installation scoped access, independent of cloud/ and billing."""
import hashlib
import hmac
import json
import os
import time
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

router = APIRouter()
COOKIE = "clipfac_session"


def enabled():
    return bool(os.environ.get("CLIPFAC_ADMIN_TOKEN"))


def credentials():
    result = []
    admin = os.environ.get("CLIPFAC_ADMIN_TOKEN", "")
    if admin:
        result.append((admin, {"read", "process", "edit", "publish", "admin"}))
    for entry in json.loads(os.environ.get("CLIPFAC_AGENT_TOKENS", "[]")):
        token = str(entry.get("token", ""))
        if len(token) < 24:
            raise ValueError("Agent tokens must contain at least 24 characters")
        scopes = set(entry.get("scopes", []))
        if not scopes <= {"read", "process", "edit", "publish"}:
            raise ValueError("Unknown agent scope")
        result.append((token, scopes))
    return result


def identity(token):
    return hashlib.sha256(token.encode()).hexdigest()[:24]


def principal(request):
    if not enabled():
        return "local", {"read", "process", "edit", "publish", "admin"}, False
    token = request.headers.get("authorization", "")
    token = token[7:] if token.lower().startswith("bearer ") else request.headers.get("x-api-key", "")
    if token:
        for candidate, scopes in credentials():
            if hmac.compare_digest(token, candidate):
                return identity(candidate), scopes, False
        return None
    cookie = request.cookies.get(COOKIE, "")
    try:
        owner, expiry, sig = cookie.split(".")
        value = f"{owner}.{expiry}"
        expected = hmac.new(os.environ["CLIPFAC_ADMIN_TOKEN"].encode(), value.encode(), hashlib.sha256).hexdigest()
        if int(expiry) < time.time() or not hmac.compare_digest(sig, expected):
            return None
        for candidate, scopes in credentials():
            if hmac.compare_digest(owner, identity(candidate)):
                return owner, scopes, True
    except (ValueError, KeyError):
        pass
    return None


def request_owner(request):
    user = principal(request)
    if not user:
        raise HTTPException(401, "Connect an installation token")
    # Role tokens share one installation so editing, QA and publishing can hand off work.
    return os.environ.get("CLIPFAC_WORKSPACE_ID", "local")


def required_scope(path, method, tool=None):
    if tool:
        from clipfac.mcp import TOOL_SCOPES
        old = {"process_video": "process", "create_upload": "process",
               "add_subtitles": "edit", "recut_clip": "edit", "publish_clip": "publish",
               "get_job_status": "read", "list_clips": "read", "get_quota": "read"}
        return TOOL_SCOPES.get(tool, old.get(tool, "admin"))
    if method in ("GET", "HEAD", "OPTIONS"):
        return "read"
    if path in ("/api/clipfac/inspect", "/api/clipfac/brief", "/api/clipfac/reviews"):
        return "read"
    if path == "/api/clipfac/publish":
        return "publish"
    if path.startswith(("/api/social/", "/api/thumbnail/publish", "/api/saasshorts/post")):
        return "publish"
    if path == "/api/clipfac/plans/render" or path == "/api/clipfac/analyses":
        return "process"
    if path.startswith(("/api/clipfac/plans", "/api/clipfac/reviews/repair")):
        return "edit"
    if path.startswith(("/api/process", "/api/uploads", "/api/clipfac/productions",
                        "/api/saasshorts/generate", "/api/saasshorts/analyze")):
        return "process"
    if path.startswith(("/api/clip/", "/api/edit", "/api/subtitle", "/api/hook", "/api/translate",
                        "/api/render", "/api/effects")):
        return "edit"
    return "admin"


class InstallationAuth(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        # CORS preflights carry no credentials and change nothing. Let the
        # CORS layer answer them instead of rejecting them for having no
        # token: a rejected preflight makes every cross-origin call fail in
        # the browser even though the server would have answered it.
        if request.method == "OPTIONS":
            return await call_next(request)
        path = request.url.path
        protected = path.startswith(("/api/", "/videos/", "/thumbnails/", "/mcp", "/gallery", "/video/"))
        if not enabled() or not protected or path in ("/api/config", "/api/clipfac/session"):
            return await call_next(request)
        user = principal(request)
        if not user:
            return JSONResponse({"detail": "Installation token required"}, status_code=401, headers=_cors_headers(request))
        owner, scopes, cookie_auth = user
        if cookie_auth and request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if not origin or urlparse(origin).netloc != request.url.netloc:
                return JSONResponse({"detail": "Same-origin request required"}, status_code=403, headers=_cors_headers(request))
        scope = required_scope(path, request.method)
        if path.rstrip("/") == "/mcp":
            scope = "read"
            if request.method == "POST":
                try:
                    message = await request.json()
                    if isinstance(message, dict) and message.get("method") == "tools/call":
                        params = message.get("params") or {}
                        name = params.get("name") if isinstance(params, dict) else None
                        scope = required_scope(path, "POST", name if isinstance(name, str) else "unknown")
                except (ValueError, TypeError):
                    pass  # MCP returns its own parse error.
        if scope not in scopes and "admin" not in scopes:
            return JSONResponse({"detail": f"Token requires {scope} scope"}, status_code=403, headers=_cors_headers(request))
        request.state.clipfac_owner = owner
        return await call_next(request)


def _cors_headers(request):
    """CORS headers for InstallationAuth's own rejections.

    This middleware is added after CORSMiddleware, so it runs first and its
    early 401/403 responses never pass through the CORS layer. Without these
    headers the browser hides the rejection from fetch(): the workspace can
    neither read the 401 (so no login screen ever appears) nor tell a denied
    call from a network failure. The values mirror what CORSMiddleware sends
    for allowed origins on this self-host install (origin echoed because
    credentials are allowed).
    """
    origin = request.headers.get("origin")
    if not origin:
        return {}
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Credentials": "true",
        "Vary": "Origin",
    }


@router.post("/api/clipfac/session")
async def session(request: Request):
    """Exchange a header token for a same-origin HttpOnly media/UI session."""
    if not enabled():
        return {"authenticated": True, "mode": "local"}
    # Cookies cannot mint a new session: the caller must present the token again.
    if not request.headers.get("authorization") and not request.headers.get("x-api-key"):
        raise HTTPException(401, "Installation token required")
    user = principal(request)
    if not user:
        raise HTTPException(401, "Invalid installation token")
    owner, scopes, _ = user
    expiry = str(int(time.time()) + 43200)
    value = f"{owner}.{expiry}"
    signature = hmac.new(os.environ["CLIPFAC_ADMIN_TOKEN"].encode(), value.encode(), hashlib.sha256).hexdigest()
    response = JSONResponse({"authenticated": True, "scopes": sorted(scopes)})
    response.set_cookie(COOKIE, f"{value}.{signature}", httponly=True, secure=request.url.scheme == "https",
                        samesite="strict", max_age=43200, path="/")
    return response


@router.delete("/api/clipfac/session")
async def logout():
    response = JSONResponse({"authenticated": False})
    response.delete_cookie(COOKIE, path="/")
    return response
