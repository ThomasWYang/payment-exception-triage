"""FastAPI application: auth, validation, error mapping and operational endpoints."""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from payment_triage.config import Identity, Settings, load_settings
from payment_triage.db import connect, init_schema
from payment_triage.executor import MockPaymentExecutor
from payment_triage.logging import configure_logging
from payment_triage.models import (
    ApprovalResult,
    ApproveRequest,
    AuditList,
    CaseList,
    CaseStatus,
    CaseView,
    CreateCaseRequest,
    Role,
    TriageView,
)
from payment_triage.planner import DeterministicPlanner
from payment_triage.policy import PolicyEngine
from payment_triage.repository import Repository
from payment_triage.tools import load_policy_docs
from payment_triage.workflow import Workflow, WorkflowError

logger = logging.getLogger(__name__)
UI_DIST = Path(__file__).resolve().parents[2] / "ui" / "dist"


class ApiError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 400,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", uuid.uuid4().hex)


def _error_response(
    status_code: int,
    code: str,
    message: str,
    request_id: str,
    details: dict[str, object] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "details": details or {},
                "request_id": request_id,
            }
        },
        headers={"X-Request-ID": request_id},
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    current = settings or load_settings()
    configure_logging(current.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        conn = connect(current.database_path)
        init_schema(conn)
        conn.close()
        load_policy_docs(current.policy_path)
        app.state.started_at = time.monotonic()
        yield

    app = FastAPI(
        title="Payment Exception Triage",
        version=current.app_version,
        lifespan=lifespan,
    )
    app.state.settings = current
    app.state.started_at = time.monotonic()
    app.state.auth_denials = 0
    app.state.auth_denials_lock = threading.Lock()

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        request.state.request_id = request_id

        def reject(
            status_code: int,
            code: str,
            message: str,
            details: dict[str, object] | None = None,
        ) -> JSONResponse:
            logger.info(
                "request_rejected",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": status_code,
                    "code": code,
                },
            )
            return _error_response(
                status_code, code, message, request_id, details
            )

        transfer_encoding = request.headers.get("transfer-encoding", "").lower()
        if "chunked" in transfer_encoding:
            return reject(
                411, "LENGTH_REQUIRED", "Chunked requests are not supported"
            )
        content_length = request.headers.get("content-length")
        if content_length is None and request.method in {"POST", "PUT", "PATCH"}:
            return reject(411, "LENGTH_REQUIRED", "Content-Length is required")
        if content_length is not None:
            try:
                length = int(content_length)
            except ValueError:
                return reject(
                    400, "INVALID_CONTENT_LENGTH", "Invalid Content-Length"
                )
            if length > current.max_request_bytes:
                return reject(
                    413,
                    "REQUEST_TOO_LARGE",
                    "Request body is too large",
                    {"max_request_bytes": current.max_request_bytes},
                )
        started = time.monotonic()
        logger.info(
            "request_started",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
            },
        )
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
        finally:
            latency_ms = round((time.monotonic() - started) * 1000, 2)
            logger.info(
                "request_completed",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": status_code,
                    "latency_ms": latency_ms,
                },
            )
        response.headers["X-Request-ID"] = request_id
        return response

    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        return _error_response(
            exc.status_code, exc.code, exc.message, _request_id(request), exc.details
        )

    @app.exception_handler(WorkflowError)
    async def handle_workflow_error(request: Request, exc: WorkflowError) -> JSONResponse:
        details = {"reason_codes": exc.reason_codes} if exc.reason_codes else {}
        return _error_response(
            exc.http_status, exc.code, exc.message, _request_id(request), details
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        errors = [
            {
                "loc": list(error.get("loc", [])),
                "msg": error.get("msg", ""),
                "type": error.get("type", ""),
            }
            for error in exc.errors()
        ]
        return _error_response(
            422,
            "VALIDATION_ERROR",
            "Request validation failed",
            _request_id(request),
            {"errors": errors},
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(
            exc.status_code, "HTTP_ERROR"
        )
        response = _error_response(
            exc.status_code, code, str(exc.detail), _request_id(request)
        )
        if exc.headers:
            for name, value in exc.headers.items():
                response.headers[name] = value
        return response

    @app.exception_handler(Exception)
    async def handle_unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_error", extra={"request_id": _request_id(request)})
        response = _error_response(
            500,
            "INTERNAL_ERROR",
            "An internal error occurred",
            _request_id(request),
        )
        origin = request.headers.get("origin")
        if origin in current.cors_origins:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Vary"] = "Origin"
        return response

    def get_repo(request: Request) -> Iterator[Repository]:
        conn = connect(request.app.state.settings.database_path)
        try:
            yield Repository(conn)
        finally:
            conn.close()

    def get_workflow(
        request: Request, repo: Repository = Depends(get_repo)
    ) -> Workflow:
        app_settings = request.app.state.settings
        return Workflow(
            repo=repo,
            settings=app_settings,
            planner=DeterministicPlanner(app_settings.refund_cap_cents),
            policy=PolicyEngine(app_settings),
            executor=MockPaymentExecutor(app_settings.executor_mode),
        )

    def require_identity(
        request: Request,
        api_key: str | None = Header(default=None, alias="X-API-Key"),
    ) -> Identity:
        identity = request.app.state.settings.identity_for(api_key)
        if identity is None:
            with request.app.state.auth_denials_lock:
                request.app.state.auth_denials += 1
            logger.warning(
                "auth_denied",
                extra={
                    "request_id": _request_id(request),
                    "path": request.url.path,
                },
            )
            raise ApiError("UNAUTHORIZED", "Missing or invalid API key", 401)
        return identity

    def select_acting_role(
        identity: Identity, raw: str | None, allowed: tuple[Role, ...]
    ) -> Identity:
        if raw is None or not raw.strip():
            for role in allowed:
                if role in identity.roles:
                    return identity.with_active_role(role)
            raise ApiError("FORBIDDEN", "Role not permitted", 403)
        try:
            selected = Role(raw.strip().upper())
        except ValueError as exc:
            raise ApiError("INVALID_ACTING_ROLE", "Unknown X-Acting-Role", 400) from exc
        if selected not in identity.roles:
            raise ApiError("FORBIDDEN", f"Role {selected.value} not held", 403)
        if selected not in allowed:
            raise ApiError(
                "ACTIVE_ROLE_MISMATCH",
                "Active role is not permitted for this endpoint",
                403,
            )
        return identity.with_active_role(selected)

    def require_role(role: Role) -> Callable[..., Identity]:
        def dependency(
            identity: Identity = Depends(require_identity),
            acting_role: str | None = Header(default=None, alias="X-Acting-Role"),
        ) -> Identity:
            return select_acting_role(identity, acting_role, (role,))

        return dependency

    def require_any_role(*roles: Role) -> Callable[..., Identity]:
        def dependency(
            identity: Identity = Depends(require_identity),
            acting_role: str | None = Header(default=None, alias="X-Acting-Role"),
        ) -> Identity:
            return select_acting_role(identity, acting_role, roles)

        return dependency

    @app.post("/v1/cases", response_model=CaseView, status_code=201)
    def create_case(
        payload: CreateCaseRequest,
        identity: Identity = Depends(require_role(Role.ANALYST)),
        workflow: Workflow = Depends(get_workflow),
    ) -> CaseView:
        case = workflow.create_case(payload, identity)
        return CaseView(case=case)

    @app.post("/v1/cases/{case_id}/triage", response_model=TriageView)
    def triage(
        case_id: str,
        identity: Identity = Depends(require_any_role(Role.ANALYST, Role.AGENT)),
        workflow: Workflow = Depends(get_workflow),
    ) -> TriageView:
        return workflow.triage(case_id, identity)

    @app.get("/v1/cases/{case_id}", response_model=CaseView)
    def get_case(
        case_id: str,
        identity: Identity = Depends(require_any_role(Role.ANALYST, Role.APPROVER)),
        workflow: Workflow = Depends(get_workflow),
    ) -> CaseView:
        return workflow.get_case_view(case_id)

    @app.get("/v1/cases", response_model=CaseList)
    def list_cases(
        status: CaseStatus | None = None,
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        identity: Identity = Depends(require_any_role(Role.ANALYST, Role.APPROVER)),
        workflow: Workflow = Depends(get_workflow),
    ) -> CaseList:
        items = workflow.list_case_views(status, limit, offset)
        return CaseList(items=items, limit=limit, offset=offset)

    @app.post("/v1/cases/{case_id}/approve", response_model=ApprovalResult)
    def approve(
        case_id: str,
        payload: ApproveRequest,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        identity: Identity = Depends(require_role(Role.APPROVER)),
        workflow: Workflow = Depends(get_workflow),
    ) -> ApprovalResult:
        return workflow.approve(case_id, identity, payload, idempotency_key)

    @app.get("/v1/cases/{case_id}/audit", response_model=AuditList)
    def audit(
        case_id: str,
        limit: int = Query(default=500, ge=1, le=1000),
        offset: int = Query(default=0, ge=0),
        identity: Identity = Depends(require_any_role(Role.ANALYST, Role.APPROVER)),
        workflow: Workflow = Depends(get_workflow),
    ) -> AuditList:
        return AuditList(
            items=workflow.get_audit(case_id, limit=limit, offset=offset),
            limit=limit,
            offset=offset,
        )

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz(request: Request) -> JSONResponse:
        path = request.app.state.settings.database_path
        conn = None
        try:
            conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
            conn.execute("SELECT 1 FROM cases LIMIT 1")
        except Exception:
            return JSONResponse(status_code=503, content={"status": "unavailable"})
        finally:
            if conn is not None:
                conn.close()
        return JSONResponse(status_code=200, content={"status": "ready"})

    @app.get("/status")
    def service_status(
        request: Request, workflow: Workflow = Depends(get_workflow)
    ) -> dict[str, object]:
        app_settings = request.app.state.settings
        if app_settings.degraded_mode:
            mode = "degraded"
        elif not app_settings.auto_approve_enabled:
            mode = "auto_approve_disabled"
        else:
            mode = "normal"
        payload: dict[str, object] = {
            "version": app_settings.app_version,
            "mode": mode,
            "uptime_seconds": round(time.monotonic() - request.app.state.started_at, 1),
            "auth_denials": request.app.state.auth_denials,
            "auto_retry_limit_cents": app_settings.auto_retry_limit_cents,
            "refund_cap_cents": app_settings.refund_cap_cents,
            "auto_approve_enabled": app_settings.auto_approve_enabled,
            "degraded_mode": app_settings.degraded_mode,
        }
        payload.update(workflow.status())
        return payload

    ui_mounted = UI_DIST.is_dir()
    if ui_mounted:
        app.mount("/ui", StaticFiles(directory=UI_DIST, html=True), name="ui")

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse(url="/ui/" if ui_mounted else "/docs")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(current.cors_origins),
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )
    return app
