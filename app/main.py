"""CAMPUS DESK / AIDTM Student Support Agent: FastAPI application factory.

Run:  python run.py   (or: uvicorn app.main:app)   Production: gunicorn -k uvicorn.workers.UvicornWorker app.main:app  (see README)
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select, text

from app import config, db
from app.logging_config import logger
from app.security.context import correlation_id
from app.security.headers import SecurityHeadersMiddleware
from app.services import authorization_service as authz


@asynccontextmanager
async def lifespan(app: FastAPI):
    problems = config.validate_startup()
    if problems:
        raise RuntimeError("Refusing to start: " + "; ".join(problems))
    db.get_engine()
    ok, reason = config.llm_configured()
    logger.info("%s v%s env=%s llm=%s", config.APP_NAME, config.APP_VERSION, config.ENV, reason if ok else f"NOT CONFIGURED ({reason}); chat is disabled, everything else works")
    yield


def create_app() -> FastAPI:
    app = FastAPI(title=config.APP_NAME, version=config.APP_VERSION, lifespan=lifespan,
                  docs_url="/docs" if config.ENABLE_API_DOCS else None, redoc_url=None, openapi_url="/openapi.json" if config.ENABLE_API_DOCS else None)
    app.add_middleware(SecurityHeadersMiddleware)

    from app.routes import admin_routes, approval_routes, auth_routes, chat_routes, memory_routes, policy_routes, student_routes
    for r in (auth_routes.router, chat_routes.router, student_routes.router, memory_routes.router, approval_routes.router, admin_routes.router, policy_routes.router):
        app.include_router(r)

    def err(status: int, code: str, message: str, **extra) -> JSONResponse:
        return JSONResponse(status_code=status, content={"error": {"code": code, "message": message, **extra}})

    @app.exception_handler(HTTPException)
    async def http_exc(request: Request, exc: HTTPException):
        d = exc.detail if isinstance(exc.detail, dict) else {"code": "HTTP_ERROR", "message": str(exc.detail)}
        return JSONResponse(status_code=exc.status_code, content={"error": d}, headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def validation(request: Request, exc: RequestValidationError):
        # field names and messages only; the submitted input is never echoed back (it may contain secrets)
        problems = [{"field": ".".join(str(p) for p in e["loc"] if p != "body"), "message": e["msg"]} for e in exc.errors()[:6]]
        return err(422, "VALIDATION_ERROR", "The request was not valid.", problems=problems)

    @app.exception_handler(authz.Forbidden)
    async def forbidden(request: Request, exc: authz.Forbidden):
        try:
            with db.session_scope() as s:                       # the request session is already closed/rolled back here
                authz.record_denied(s, getattr(request.state, "principal", None), exc, commit=False)
        except Exception:                                       # noqa: BLE001
            logger.exception("could not audit a denied request")
        return err(403, "FORBIDDEN", authz.FORBIDDEN_MESSAGE)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        ref = correlation_id()
        logger.exception("unhandled error (reference %s) on %s %s", ref, request.method, request.url.path)
        return err(500, "INTERNAL_ERROR", "Something went wrong on our side.", reference=ref)

    @app.get("/api/health", tags=["health"])
    def health():
        ok, reason = config.llm_configured()
        out = {"status": "ok", "version": config.APP_VERSION, "llm": {"provider": config.LLM_PROVIDER, "configured": ok}}
        try:
            with db.session_scope() as s:
                s.execute(text("select 1"))
                from app.models import PolicyChunk
                out["database"] = "ok"; out["policy_chunks"] = s.execute(select(func.count()).select_from(PolicyChunk)).scalar_one()
        except Exception:                                       # noqa: BLE001
            out["status"], out["database"] = "degraded", "unavailable"
        if not ok:
            out["llm"]["note"] = "AI chat disabled until configured"
        return out

    app.mount("/static", StaticFiles(directory=str(config.STATIC_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(str(config.STATIC_DIR / "index.html"))

    return app


app = create_app()
