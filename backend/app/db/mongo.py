"""MongoDB client lifecycle — resume *file bytes* storage only
(app/integrations/storage/mongo_gridfs.py). Never a system of record for
candidate/application/organization data; that stays in Postgres
(CLAUDE.md § 1-2).

One `AsyncMongoClient` for the whole process, created in `app.main`'s
lifespan on startup and closed on shutdown — never per-request (PyMongo's
async client pools connections internally exactly like the sync one; a new
client per request would defeat that pool and leak sockets).
"""

from typing import Any

from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.server_api import ServerApi

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_client: AsyncMongoClient[dict[str, Any]] | None = None


async def connect_mongo() -> None:
    """Creates the process-wide client and verifies connectivity with a
    `ping` (per MongoDB Stable API guidance). Never logs `mongodb_uri` —
    it may contain credentials."""
    global _client

    settings = get_settings()
    if not settings.mongodb_uri:
        raise RuntimeError("MONGODB_URI is not configured.")

    client: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(
        settings.mongodb_uri, server_api=ServerApi("1")
    )
    try:
        await client.admin.command("ping")
    except Exception:
        await client.close()
        logger.exception("MongoDB connection failed during startup")
        raise

    _client = client
    logger.info("MongoDB connection established")


async def connect_mongo_for_legacy_reads() -> bool:
    """Best-effort connection used when new resume files go to local disk
    (`RESUME_STORAGE_PROVIDER=local`) but `MONGODB_URI` is still set, so
    Resume rows written earlier under `mongodb_gridfs` keep resolving
    (each row is read through its own `storage_provider` —
    app/integrations/storage/__init__.py).

    Unlike `connect_mongo`, a failure here never stops the API from
    starting: MongoDB is not the active storage backend, so only those
    older files become unavailable — and reading one reports a storage
    error (app/integrations/storage/mongo_gridfs.py), never a 500.
    Returns whether the connection was established."""
    try:
        await connect_mongo()
    except Exception:
        logger.warning(
            "MongoDB is unreachable; resume files stored in GridFS by an earlier "
            "configuration are unavailable until it is reachable and the API restarts"
        )
        return False
    return True


async def disconnect_mongo() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None


def get_mongo_client() -> AsyncMongoClient[dict[str, Any]]:
    if _client is None:
        raise RuntimeError(
            "MongoDB client is not initialized — connect_mongo() must run at "
            "startup before this is called (see app/main.py's lifespan)."
        )
    return _client


def get_mongo_database() -> AsyncDatabase[dict[str, Any]]:
    settings = get_settings()
    return get_mongo_client()[settings.mongodb_database]


async def ping_mongo() -> bool:
    """Readiness-check helper: reports reachability without raising. Does
    not run on every request — only from /readyz (app/api/system.py)."""
    if _client is None:
        return False
    try:
        await _client.admin.command("ping")
        return True
    except Exception:
        logger.exception("MongoDB readiness check failed")
        return False
