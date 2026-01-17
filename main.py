from fastapi import FastAPI
from .routers import health, outbound_twillio


def create_app() -> FastAPI:
    app = FastAPI(title="VakLab AI Agents")
    app.include_router(health.router)
    app.include_router(outbound_twillio.router)

    return app


app = create_app()