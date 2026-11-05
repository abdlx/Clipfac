"""Small installation seam; upstream processing and cloud licensing stay intact."""
import os
from clipfac.api import router
from clipfac.pipeline_api import router as pipeline_router
from clipfac.review_api import router as review_router
from clipfac.publishing import router as publishing_router
from clipfac.vault import ProviderHeaders, apply_runtime, router as provider_router
from clipfac.auth import InstallationAuth, credentials, router as auth_router


def install(app, namespace):
    if os.environ.get("BILLING_ENABLED", "").lower() in ("1", "true", "yes"):
        return
    token = os.environ.get("CLIPFAC_ADMIN_TOKEN", "")
    if token and len(token) < 24:
        raise ValueError("CLIPFAC_ADMIN_TOKEN must contain at least 24 characters")
    if os.environ.get("CLIPFAC_REQUIRE_AUTH", "0") == "1" and not token:
        raise ValueError("Set CLIPFAC_ADMIN_TOKEN before exposing Clipfac")
    credentials()  # Fail on malformed agent credentials at startup.
    apply_runtime()
    app.state.clipfac_namespace = namespace
    app.include_router(router)
    app.include_router(pipeline_router)
    app.include_router(review_router)
    app.include_router(provider_router)
    app.include_router(publishing_router)
    app.add_middleware(ProviderHeaders)
    app.include_router(auth_router)
    app.add_middleware(InstallationAuth)
