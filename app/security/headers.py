"""Security headers + request ID middleware (pure ASGI, so it also wraps streaming responses)."""
from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app import config
from app.security.context import correlation_id_var, new_id

CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
       "font-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
DOCS_CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "img-src 'self' data: https://fastapi.tiangolo.com; frame-ancestors 'none'")


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        cid = new_id("REQ-", 8)
        correlation_id_var.set(cid)
        path = scope.get("path", "")
        is_docs = path.startswith(("/docs", "/redoc", "/openapi"))

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                def add(k: str, v: str) -> None: headers.append((k.encode(), v.encode()))
                add("X-Request-ID", cid)
                add("X-Content-Type-Options", "nosniff")
                add("X-Frame-Options", "DENY")
                add("Referrer-Policy", "no-referrer")
                add("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
                add("Cross-Origin-Opener-Policy", "same-origin")
                add("Content-Security-Policy", DOCS_CSP if is_docs else CSP)
                if path.startswith("/api/"):
                    add("Cache-Control", "no-store")
                if config.SESSION_COOKIE_SECURE:
                    add("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
            await send(message)

        await self.app(scope, receive, send_wrapper)
