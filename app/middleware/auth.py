from typing import Callable, Iterable, List, Pattern
import re
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.responses import JSONResponse
from app.core.config import get_settings

HEADER_NAME = "x-api-key"


def _template_to_regex(path_template: str) -> Pattern:
    # Convert e.g. "/api/users/by-qr/{qrId}" -> r"^/api/users/by-qr/[^/]+$"
    pattern = re.sub(r"\{[^/]+\}", r"[^/]+", path_template)
    return re.compile(f"^{pattern}$")


class ApiKeyAuthMiddleware:
    """
    Requires header 'x-api-key' to match settings.API_KEY for all routes,
    EXCEPT those whitelisted as public.
    """

    def __init__(self, app: ASGIApp, public_paths: Iterable[str]):
        self.app = app
        self.settings = get_settings()

        # Exact public paths (match one URL only)
        always_public_exact = ["/redoc",
                               "/healthz", "/docs"]  # keep /docs exact too
        self._public_patterns: List[Pattern] = [
            _template_to_regex(p) for p in public_paths]
        self._public_patterns.extend(_template_to_regex(p)
                                     for p in always_public_exact)

        # Prefix public paths (match any URL starting with these)
        # Make all static docs public, e.g. /docs/, /docs/index.html, /docs/assets/...
        self._public_prefixes: List[str] = ["/docs/"]

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path: str = scope.get("path", "")
        method: str = scope.get("method", "GET")

        # Allow CORS preflight
        if method == "OPTIONS":
            await self.app(scope, receive, send)
            return

        # Public if path matches an exact public pattern OR begins with a public prefix
        if any(p.match(path) for p in self._public_patterns) or any(
            path.startswith(prefix) for prefix in self._public_prefixes
        ):
            await self.app(scope, receive, send)
            return

        # Require API key
        headers = {k.decode().lower(): v.decode()
                   for k, v in scope.get("headers", [])}
        provided = headers.get(HEADER_NAME)
        if not provided or provided != self.settings.API_KEY:
            resp = JSONResponse(
                status_code=401,
                content={"status": "error",
                         "message": "Unauthorized: missing or invalid x-api-key"},
            )
            await resp(scope, receive, send)
            return

        await self.app(scope, receive, send)


def collect_public_paths(app) -> List[str]:
    """Gather path templates where endpoint has attribute `is_public = True`."""
    public_paths: List[str] = []
    try:
        for route in app.router.routes:
            endpoint = getattr(route, "endpoint", None)
            path = getattr(route, "path", None)
            if endpoint and path and getattr(endpoint, "is_public", False):
                public_paths.append(path)
    except Exception:
        pass
    return public_paths


def public(endpoint: Callable) -> Callable:
    """Decorator to mark an endpoint as public (no API key required)."""
    setattr(endpoint, "is_public", True)
    return endpoint
