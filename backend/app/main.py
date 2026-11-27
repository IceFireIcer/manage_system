from fastapi import FastAPI

from app.config import get_settings
from app.routers import auth, users


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        debug=settings.debug,
    )

    app.include_router(auth.router)
    app.include_router(users.router)

    @app.get("/api/v1/health")
    def health_check() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
