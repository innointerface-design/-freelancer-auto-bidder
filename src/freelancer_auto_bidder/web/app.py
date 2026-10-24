"""FastAPI presentation layer for the preserved Python bidding engine."""

import hmac
import logging
import os
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictBool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .repository import WebRepository
from .scheduler import Monitor
from .security import AuthError, AuthManager
from .service import WebService, WebServiceError
from .settings import WebSettings, load_web_settings

logger = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"
COOKIE = "bidder_session"


class LoginInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=128)
    password: SecretStr = Field(min_length=1, max_length=1024)


class SearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(default="", max_length=200)


class ControlsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    monitor_enabled: StrictBool | None = None
    auto_bidder_enabled: StrictBool | None = None


class GenerateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instructions: str = Field(default="", max_length=4000)


class DraftInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=8000)


def create_app(
    settings: WebSettings | None = None, *, service: WebService | None = None
) -> FastAPI:
    """Factory for Uvicorn and tests. Importing this module never starts a worker."""
    settings = settings or load_web_settings()
    settings.validate()
    repository = WebRepository(settings.database_path)
    auth = AuthManager(
        settings.database_path,
        settings.admin_username,
        settings.admin_password_hash,
        session_ttl_seconds=settings.session_ttl_seconds,
    )
    service = service or WebService(settings, repository)
    monitor = Monitor(service, interval_seconds=settings.engine.poll_interval_seconds)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # SQLite protects individual writes; this lease also prevents two web
        # processes from independently running the same polling schedule.
        import fcntl

        descriptor = os.open(str(settings.database_path) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("Another web worker owns this database; run exactly one worker")
            repository.set_controls(auto_bidder_enabled=False)
            repository.event("Dashboard started; automatic bidder is off")
            if settings.start_monitor_thread:
                monitor.start()
            try:
                yield
            finally:
                service.stop()
                monitor.stop()
                repository.set_runtime(next_run_at=None)
        finally:
            os.close(descriptor)

    app = FastAPI(
        title="BidPilot — Freelancer Workspace",
        version="0.2.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.service = service
    app.state.repository = repository
    app.state.auth = auth
    app.state.monitor = monitor
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.allowed_hosts))

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        # Bound all request bodies, including chunked requests, before JSON parsing.
        response = None
        if request.method in {"POST", "PUT", "PATCH"}:
            size = 0
            chunks = []
            async for chunk in request.stream():
                size += len(chunk)
                if size > 65536:
                    response = JSONResponse(
                        {"detail": "Request body is too large"}, status_code=413
                    )
                    break
                chunks.append(chunk)
            if response is None:
                request._body = b"".join(chunks)
        if response is None:
            response = await call_next(request)
        response.headers.update(
            {
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
                "Referrer-Policy": "no-referrer",
                "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
                "Content-Security-Policy": (
                    "default-src 'self'; script-src 'self'; style-src 'self'; "
                    "img-src 'self' data:; font-src 'self'; connect-src 'self'; "
                    "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
                ),
                "Cache-Control": "no-store",
            }
        )
        if settings.cookie_secure and request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request: Request, _exc: RequestValidationError):
        # FastAPI's default validation response can echo passwords or prompt text.
        return JSONResponse({"detail": "Invalid request fields"}, status_code=422)

    @app.exception_handler(WebServiceError)
    async def service_error(_request: Request, exc: WebServiceError):
        return JSONResponse({"detail": str(exc)}, status_code=exc.status_code)

    @app.exception_handler(AuthError)
    async def auth_error(_request: Request, exc: AuthError):
        return JSONResponse({"detail": str(exc)}, status_code=exc.status_code)

    @app.exception_handler(sqlite3.Error)
    async def database_error(_request: Request, _exc: sqlite3.Error):
        logger.error("Dashboard database operation failed")
        return JSONResponse({"detail": "Local database is unavailable"}, status_code=503)

    def check_origin(request: Request) -> None:
        origin = request.headers.get("origin")
        expected = settings.origin or str(request.base_url).rstrip("/")
        if origin is not None and origin.rstrip("/") != expected:
            raise HTTPException(403, "Request origin is not permitted")
        if request.headers.get("sec-fetch-site") == "cross-site":
            raise HTTPException(403, "Cross-site requests are not permitted")

    def principal(request: Request) -> dict:
        user = auth.authenticate(request.cookies.get(COOKIE, ""))
        if user is None:
            raise HTTPException(401, "Sign in to continue")
        return user

    def mutation(request: Request, user: Annotated[dict, Depends(principal)]) -> dict:
        check_origin(request)
        supplied = request.headers.get("x-csrf-token", "")
        if not hmac.compare_digest(supplied.encode("utf-8"), user["csrf_token"].encode("utf-8")):
            raise HTTPException(403, "Invalid CSRF token")
        return user

    @app.get("/healthz")
    def health():
        return {"status": "ok"}

    @app.get("/")
    def index(request: Request):
        page = "index.html" if auth.authenticate(request.cookies.get(COOKIE, "")) else "login.html"
        return FileResponse(STATIC / page)

    @app.get("/login")
    def login_page():
        return FileResponse(STATIC / "login.html")

    @app.post("/api/auth/login")
    def login(body: LoginInput, request: Request):
        check_origin(request)
        if request.headers.get("x-login-intent") != "admin":
            raise HTTPException(403, "Login intent header is required")
        user = auth.login(
            body.username,
            body.password.get_secret_value(),
            request.client.host if request.client else "unknown",
        )
        # Revoke the old session on a successful login rather than reusing it.
        auth.logout(request.cookies.get(COOKIE, ""))
        response = JSONResponse({k: v for k, v in user.items() if k != "token"})
        response.set_cookie(
            COOKIE,
            user["token"],
            httponly=True,
            secure=settings.cookie_secure,
            samesite="strict",
            max_age=settings.session_ttl_seconds,
            path="/",
        )
        return response

    @app.get("/api/session")
    def session(user: Annotated[dict, Depends(principal)]):
        return user

    @app.post("/api/auth/logout")
    def logout(request: Request, _user: Annotated[dict, Depends(mutation)]):
        auth.logout(request.cookies.get(COOKIE, ""))
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE, path="/", secure=settings.cookie_secure, samesite="strict")
        return response

    @app.get("/api/dashboard")
    def dashboard(_user: Annotated[dict, Depends(principal)]):
        return service.dashboard()

    @app.get("/api/jobs")
    def jobs(
        _user: Annotated[dict, Depends(principal)],
        query: str = Query("", max_length=200),
        skill: str = Query("", max_length=100),
        countries: str = Query("", max_length=300),
        currency: str = Query("", max_length=3),
        min_budget: float | None = Query(None, ge=0),
        max_budget: float | None = Query(None, ge=0),
        decision: str = Query("", max_length=4),
        sort: str = Query("score", pattern="^(score|newest)$"),
        offset: int = Query(0, ge=0, le=100000),
        limit: int = Query(20, ge=1, le=100),
    ):
        return service.jobs(
            {
                "query": query,
                "skill": skill,
                "countries": countries,
                "currency": currency,
                "min_budget": min_budget,
                "max_budget": max_budget,
                "decision": decision,
                "sort": sort,
                "offset": offset,
                "limit": limit,
            }
        )

    @app.post("/api/search")
    def search(body: SearchInput, _user: Annotated[dict, Depends(mutation)]):
        return service.search(body.query)

    @app.post("/api/controls")
    def controls(body: ControlsInput, _user: Annotated[dict, Depends(mutation)]):
        return service.controls(body.model_dump(exclude_none=True))

    @app.get("/api/history")
    def history(_user: Annotated[dict, Depends(principal)], limit: int = Query(100, ge=1, le=1000)):
        return service.history(limit)

    @app.get("/api/settings")
    def get_settings(_user: Annotated[dict, Depends(principal)]):
        return service.get_settings()

    @app.patch("/api/settings")
    def update_settings(body: dict, _user: Annotated[dict, Depends(mutation)]):
        return service.update_settings(body)

    @app.post("/api/jobs/{project_id}/proposal")
    def proposal(project_id: int, body: GenerateInput, _user: Annotated[dict, Depends(mutation)]):
        return service.generate_proposal(project_id, body.instructions)

    @app.put("/api/jobs/{project_id}/proposal")
    def save_proposal(project_id: int, body: DraftInput, _user: Annotated[dict, Depends(mutation)]):
        return service.save_proposal(project_id, body.text)

    app.mount("/static", StaticFiles(directory=STATIC, check_dir=False), name="static")
    return app
