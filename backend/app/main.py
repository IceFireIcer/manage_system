from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.routers import auth, consumables, consumption, inventory, logs, requisitions, users
from app.security import host_is_allowed, resolve_forwarded_scheme

HTTPS_REQUIRED_DETAIL = "生产环境必须通过 HTTPS 访问"
TRUSTED_HOST_DETAIL = "非法来源主机"
LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _is_internal_health(request: Request) -> bool:
    if request.url.path != "/api/v1/health":
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
        if current.is_production and not _is_internal_health(request):
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

    app.include_router(auth.router)
    app.include_router(users.router)
    app.include_router(consumables.router)
    app.include_router(requisitions.router)
    app.include_router(inventory.router)
    app.include_router(consumption.router)
    app.include_router(logs.router)

    @app.get("/api/v1/health")
    def health_check() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
