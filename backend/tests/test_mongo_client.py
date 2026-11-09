"""MongoDB client lifecycle (app/db/mongo.py) — connect/ping/disconnect
against a fake `AsyncMongoClient`, never a real MongoDB deployment
(CLAUDE.md task § 13).
"""

import pytest

from app.db import mongo as mongo_module


class _FakeAdmin:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    async def command(self, _name: str) -> dict:
        self.calls += 1
        if self.fail:
            raise RuntimeError("simulated: no route to host")
        return {"ok": 1}


class _FakeAsyncMongoClient:
    def __init__(self, uri: str, **_kwargs) -> None:
        self.uri = uri
        self.admin = _FakeAdmin()
        self.closed = False

    def __getitem__(self, name: str) -> str:
        return f"db:{name}"

    async def close(self) -> None:
        self.closed = True


class _FailingFakeAsyncMongoClient(_FakeAsyncMongoClient):
    def __init__(self, uri: str, **kwargs) -> None:
        super().__init__(uri, **kwargs)
        self.admin.fail = True


class _FakeSettings:
    def __init__(self, mongodb_uri: str | None, mongodb_database: str = "ai_recruitment") -> None:
        self.mongodb_uri = mongodb_uri
        self.mongodb_database = mongodb_database


@pytest.fixture(autouse=True)
def _reset_client_state(monkeypatch):
    monkeypatch.setattr(mongo_module, "_client", None)
    yield
    monkeypatch.setattr(mongo_module, "_client", None)


async def test_connect_mongo_requires_configured_uri(monkeypatch) -> None:
    monkeypatch.setattr(mongo_module, "get_settings", lambda: _FakeSettings(mongodb_uri=None))
    with pytest.raises(RuntimeError, match="MONGODB_URI"):
        await mongo_module.connect_mongo()


async def test_connect_mongo_pings_and_registers_client(monkeypatch) -> None:
    monkeypatch.setattr(
        mongo_module, "get_settings", lambda: _FakeSettings(mongodb_uri="mongodb://x")
    )
    monkeypatch.setattr(mongo_module, "AsyncMongoClient", _FakeAsyncMongoClient)

    await mongo_module.connect_mongo()

    client = mongo_module.get_mongo_client()
    assert isinstance(client, _FakeAsyncMongoClient)
    assert client.admin.calls == 1
    assert mongo_module.get_mongo_database() == "db:ai_recruitment"
    assert await mongo_module.ping_mongo() is True

    await mongo_module.disconnect_mongo()
    assert client.closed is True
    with pytest.raises(RuntimeError):
        mongo_module.get_mongo_client()


async def test_connect_mongo_closes_client_and_raises_when_ping_fails(monkeypatch) -> None:
    monkeypatch.setattr(
        mongo_module, "get_settings", lambda: _FakeSettings(mongodb_uri="mongodb://x")
    )
    monkeypatch.setattr(mongo_module, "AsyncMongoClient", _FailingFakeAsyncMongoClient)

    with pytest.raises(RuntimeError):
        await mongo_module.connect_mongo()

    # A failed startup ping must not leave a half-initialized client
    # reachable to the rest of the app.
    with pytest.raises(RuntimeError):
        mongo_module.get_mongo_client()


async def test_ping_mongo_returns_false_when_never_connected() -> None:
    assert await mongo_module.ping_mongo() is False


async def test_get_mongo_client_raises_before_connect() -> None:
    with pytest.raises(RuntimeError):
        mongo_module.get_mongo_client()


async def test_connect_for_legacy_reads_registers_client(monkeypatch) -> None:
    monkeypatch.setattr(
        mongo_module, "get_settings", lambda: _FakeSettings(mongodb_uri="mongodb://x")
    )
    monkeypatch.setattr(mongo_module, "AsyncMongoClient", _FakeAsyncMongoClient)

    assert await mongo_module.connect_mongo_for_legacy_reads() is True
    assert isinstance(mongo_module.get_mongo_client(), _FakeAsyncMongoClient)

    await mongo_module.disconnect_mongo()


async def test_connect_for_legacy_reads_never_raises_when_unreachable(monkeypatch) -> None:
    # MongoDB is not the active storage backend in this mode, so an
    # unreachable deployment must not stop the API from starting — it only
    # leaves the older GridFS files unavailable.
    monkeypatch.setattr(
        mongo_module, "get_settings", lambda: _FakeSettings(mongodb_uri="mongodb://x")
    )
    monkeypatch.setattr(mongo_module, "AsyncMongoClient", _FailingFakeAsyncMongoClient)

    assert await mongo_module.connect_mongo_for_legacy_reads() is False
    with pytest.raises(RuntimeError):
        mongo_module.get_mongo_client()
