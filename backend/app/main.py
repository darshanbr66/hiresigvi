from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.system import router as system_router
from app.api.v1.router import api_router
from app.core.asyncio_compat import configure_event_loop_policy
from app.core.calendar_reminders import start_reminder_loop, stop_reminder_loop
from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.middleware import RequestContextMiddleware
from app.db.mongo import connect_mongo, connect_mongo_for_legacy_reads, disconnect_mongo

configure_event_loop_policy()

logger = get_logger(__name__)


@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
    """MongoDB (resume-file storage backend) is only ever needed when
    `RESUME_STORAGE_PROVIDER=mongodb_gridfs` — local development on the
    "local" provider must not require a MongoDB deployment to start the
    app. The client is created once here, not per request (app/db/mongo.py).

    When new files go to local disk but `MONGODB_URI` is still set, the
    client is also opened — best-effort — so resumes stored in GridFS by an
    earlier configuration remain readable (docs/deployment-vps.md § 8).
    Leave `MONGODB_URI` unset when no such files exist (a fresh database).

    Also starts the calendar-reminder poll loop (app/core/calendar_
    reminders.py) — a DB-backed alternative to a Celery/Redis worker, since
    none is provisioned on this deployment.
    """
    settings = get_settings()
    if settings.resume_storage_provider == "mongodb_gridfs":
        await connect_mongo()
    elif settings.mongodb_uri:
        await connect_mongo_for_legacy_reads()
    start_reminder_loop()
    try:
        yield
    finally:
        await stop_reminder_loop()
        # No-op when no client was opened.
        await disconnect_mongo()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="AI Recruitment Platform API",
        version="0.1.0",
        debug=settings.debug,
        lifespan=_lifespan,
    )

    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        # The Applications list reports its filtered total here; a
        # cross-origin browser can only read a response header that is exposed.
        expose_headers=["X-Total-Count"],
    )

    register_exception_handlers(app)

    app.include_router(system_router)
    app.include_router(api_router)

    return app


app = create_app()
