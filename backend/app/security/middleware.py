"""HTTP security middlewares: DNS-rebinding/CSRF protection, auth gate, and bot shield."""

from __future__ import annotations

from urllib.parse import urlparse

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.security.auth import AuthManager, SESSION_COOKIE_NAME

STATE_CHANGING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

AUTH_EXEMPT_PREFIXES = (
    "/api/health",
    "/api/version",
    "/api/auth/",
    "/robots.txt",
)


def _extract_host(url_or_host: str) -> str:
    value = (url_or_host or "").strip().lower()
    if "://" in value:
        parsed = urlparse(value)
        return (parsed.netloc or "").lower()
    return value


class SecurityMiddleware(BaseHTTPMiddleware):
    """Single outermost guard for host allow-list, CSRF origin checks, auth gate, and noindex."""

    def __init__(self, app, auth: AuthManager, allowed_hosts: set[str]) -> None:
        super().__init__(app)
        self._auth = auth
        self._allowed_hosts = {h.lower() for h in allowed_hosts}

    async def dispatch(self, request: Request, call_next):
        path = request.scope.get("path", "/")

        # 1. Host header check (DNS rebinding protection)
        if "*" not in self._allowed_hosts:
            host_header = (request.headers.get("host") or "").strip().lower()
            host_no_port = host_header.split(":")[0] if ":" in host_header and not host_header.startswith("[") else host_header
            if host_header and host_header not in self._allowed_hosts and host_no_port not in self._allowed_hosts:
                return JSONResponse({"detail": "Host not allowed"}, status_code=403)

        # 2. CSRF / Origin check on state-changing requests (skip token-authenticated /hooks/, /hub/mcp, and /v1/)
        if request.method in STATE_CHANGING_METHODS and not path.startswith(("/hooks/", "/hub/mcp", "/v1/")):
            if "*" not in self._allowed_hosts:
                origin = request.headers.get("origin")
                referer = request.headers.get("referer")
                source = _extract_host(origin or referer or "")
                if source:
                    source_no_port = source.split(":")[0] if ":" in source and not source.startswith("[") else source
                    if source not in self._allowed_hosts and source_no_port not in self._allowed_hosts:
                        return JSONResponse({"detail": "Cross-origin request rejected"}, status_code=403)

        # 3. Authentication gate for /api/* routes (except health/auth endpoints)
        if path.startswith("/api/") and not path.startswith(AUTH_EXEMPT_PREFIXES):
            if self._auth.login_required():
                token = request.cookies.get(SESSION_COOKIE_NAME)
                if not self._auth.validate_session(token):
                    return JSONResponse(
                        {
                            "detail": "Authentication required",
                            "has_admin": self._auth.has_admin(),
                        },
                        status_code=401,
                    )

        response = await call_next(request)
        response.headers.setdefault("X-Robots-Tag", "noindex, nofollow")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        return response
