import logging
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.config import get_settings
from app.observability import record_error
from app.request_context import (
    REQUEST_ID_HEADER,
    incoming_request_id,
    log_tag,
    new_request_id,
    reset_request_id,
    set_request_id,
)
from app.routers import (
    accounts,
    alerts,
    assistant,
    auth,
    bootstrap,
    classes,
    consumables,
    consumption,
    courses,
    handover,
    incidents,
    inventory,
    llm,
    logs,
    ops,
    permissions,
    profile,
    requisitions,
    setup,
    users,
)
from app.security import host_is_allowed, resolve_forwarded_scheme

HTTPS_REQUIRED_DETAIL = "生产环境必须通过 HTTPS 访问"
TRUSTED_HOST_DETAIL = "非法来源主机"
DATABASE_UNAVAILABLE_DETAIL = "数据库暂时不可用，请稍后重试"
UNEXPECTED_ERROR_DETAIL = "服务内部错误，请联系管理员并提供请求编号"
LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
INTERNAL_PROBE_PATHS = {
    "/api/v1/health",
    "/api/v1/bootstrap/readiness",
    "/api/v1/ops/observability",
}

access_logger = logging.getLogger("app.request")


def _is_internal_probe(request: Request) -> bool:
    if request.url.path not in INTERNAL_PROBE_PATHS:
        return False
    client_host = request.client.host if request.client else ""
    return client_host in LOOPBACK_HOSTS


def create_app() -> FastAPI:
    settings = get_settings()
    settings.validate_production()
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        debug=settings.debug,
    )

    @app.middleware("http")
    async def production_security_guard(request: Request, call_next):
        current = get_settings()
        if current.is_production and not _is_internal_probe(request):
            client_host = request.client.host if request.client else ""
            scheme, proxy_error = resolve_forwarded_scheme(
                request.url.scheme,
                request.headers.get("x-forwarded-proto"),
                client_host,
                current.trusted_proxy_set,
            )
            if proxy_error:
                return JSONResponse(status_code=400, content={"detail": proxy_error})
            if scheme != "https":
                return JSONResponse(status_code=400, content={"detail": HTTPS_REQUIRED_DETAIL})
            if not host_is_allowed(request.headers.get("host", ""), current.trusted_host_set):
                return JSONResponse(status_code=400, content={"detail": TRUSTED_HOST_DETAIL})

        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        if current.is_production:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response

    @app.middleware("http")
    async def request_observability(request: Request, call_next):
        request_id = incoming_request_id(request.headers.get(REQUEST_ID_HEADER))
        if request_id is None:
            request_id = new_request_id()
        token = set_request_id(request_id)
        started = time.perf_counter()
        status_code = 500
        error_recorded = False
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers[REQUEST_ID_HEADER] = request_id
            return response
        except SQLAlchemyError:
            access_logger.exception(
                "%s 数据库访问失败 %s %s", log_tag(), request.method, request.url.path
            )
            record_error()
            error_recorded = True
            return JSONResponse(
                status_code=503,
                content={"detail": DATABASE_UNAVAILABLE_DETAIL, "request_id": request_id},
                headers={REQUEST_ID_HEADER: request_id},
            )
        except Exception:
            access_logger.exception(
                "%s 未处理异常 %s %s", log_tag(), request.method, request.url.path
            )
            record_error()
            error_recorded = True
            return JSONResponse(
                status_code=500,
                content={"detail": UNEXPECTED_ERROR_DETAIL, "request_id": request_id},
                headers={REQUEST_ID_HEADER: request_id},
            )
        finally:
            if status_code >= 500 and not error_recorded:
                record_error()
            access_logger.info(
                "%s %s %s -> %s %.1fms",
                log_tag(),
                request.method,
                request.url.path,
                status_code,
                (time.perf_counter() - started) * 1000,
            )
            reset_request_id(token)

    app.include_router(auth.router)
    app.include_router(bootstrap.router)
    app.include_router(setup.router)
    app.include_router(classes.router)
    app.include_router(accounts.router)
    app.include_router(users.router)
    app.include_router(profile.router)
    app.include_router(assistant.router)
    app.include_router(consumables.router)
    app.include_router(courses.router)
    app.include_router(handover.router)
    app.include_router(incidents.router)
    app.include_router(requisitions.router)
    app.include_router(inventory.router)
    app.include_router(alerts.router)
    app.include_router(consumption.router)
    app.include_router(logs.router)
    app.include_router(llm.router)
    app.include_router(ops.router)
    app.include_router(permissions.router)

    @app.get("/api/v1/health")
    def health_check() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
