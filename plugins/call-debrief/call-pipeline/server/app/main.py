from fastapi import FastAPI
from .db.session import init_db
from .routes import admin, health, jobs, recordings, web_admin, web_auth


def create_app() -> FastAPI:
    init_db()
    app = FastAPI(title="call-pipeline", version="0.1.0")
    app.include_router(health.router)
    app.include_router(jobs.router)
    app.include_router(admin.router)
    app.include_router(recordings.router)
    app.include_router(web_auth.router)
    app.include_router(web_admin.router)
    return app


app = create_app()
