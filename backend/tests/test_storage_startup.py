"""Which MongoDB connection the API opens at startup for each resume
storage configuration (app/main.py `_lifespan`), and how reading a GridFS
file behaves when no MongoDB client exists (docs/deployment-vps.md § 8).

Connection functions are replaced with recorders — no real MongoDB and no
reminder loop are involved.
"""

from types import SimpleNamespace

import pytest

import app.main as main_module
from app.db import mongo as mongo_module
from app.integrations.storage import StorageError, get_resume_storage_for_provider


@pytest.fixture
def startup_calls(monkeypatch) -> list[str]:
    calls: list[str] = []

    async def connect() -> None:
        calls.append("connect")

    async def connect_legacy() -> bool:
        calls.append("connect_legacy")
        return True

    async def disconnect() -> None:
        calls.append("disconnect")

    async def stop_loop() -> None:
        calls.append("stop_loop")

    monkeypatch.setattr(main_module, "connect_mongo", connect)
    monkeypatch.setattr(main_module, "connect_mongo_for_legacy_reads", connect_legacy)
    monkeypatch.setattr(main_module, "disconnect_mongo", disconnect)
    monkeypatch.setattr(main_module, "start_reminder_loop", lambda: calls.append("start_loop"))
    monkeypatch.setattr(main_module, "stop_reminder_loop", stop_loop)
    return calls


def _use_settings(monkeypatch, *, provider: str, mongodb_uri: str | None) -> None:
    settings = SimpleNamespace(resume_storage_provider=provider, mongodb_uri=mongodb_uri)
    monkeypatch.setattr(main_module, "get_settings", lambda: settings)


async def _run_lifespan() -> None:
    async with main_module._lifespan(main_module.app):
        pass


async def test_gridfs_provider_requires_mongodb(monkeypatch, startup_calls) -> None:
    _use_settings(monkeypatch, provider="mongodb_gridfs", mongodb_uri="mongodb://x")
    await _run_lifespan()
    assert startup_calls == ["connect", "start_loop", "stop_loop", "disconnect"]


async def test_local_provider_with_mongodb_uri_connects_for_older_files(
    monkeypatch, startup_calls
) -> None:
    _use_settings(monkeypatch, provider="local", mongodb_uri="mongodb://x")
    await _run_lifespan()
    assert startup_calls == ["connect_legacy", "start_loop", "stop_loop", "disconnect"]


async def test_local_provider_without_mongodb_uri_never_touches_mongodb(
    monkeypatch, startup_calls
) -> None:
    # The fresh VPS configuration: local disk, no MONGODB_URI.
    _use_settings(monkeypatch, provider="local", mongodb_uri=None)
    await _run_lifespan()
    assert "connect" not in startup_calls
    assert "connect_legacy" not in startup_calls


def test_gridfs_storage_without_client_raises_storage_error(monkeypatch) -> None:
    # Every caller maps StorageError to a "file unavailable" response, so an
    # older GridFS resume never turns into a 500 when MongoDB isn't connected.
    monkeypatch.setattr(mongo_module, "_client", None)
    with pytest.raises(StorageError):
        get_resume_storage_for_provider("mongodb_gridfs")
