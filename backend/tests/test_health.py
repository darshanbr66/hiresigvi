from httpx import AsyncClient


async def test_healthz_ok(client: AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readyz_ok(client: AsyncClient) -> None:
    response = await client.get("/readyz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"


# --- /readyz in production storage mode (RESUME_STORAGE_PROVIDER=mongodb_gridfs)
# MongoDB itself is replaced by a stubbed ping: no real deployment is contacted.


def _gridfs_mode(monkeypatch, *, mongo_reachable: bool) -> None:
    import app.api.system as system_module

    settings = system_module.get_settings().model_copy(
        update={"resume_storage_provider": "mongodb_gridfs", "mongodb_uri": "mongodb://stub"}
    )
    monkeypatch.setattr(system_module, "get_settings", lambda: settings)

    async def ping() -> bool:
        return mongo_reachable

    monkeypatch.setattr(system_module, "ping_mongo", ping)


async def test_readyz_checks_mongodb_in_gridfs_mode(client: AsyncClient, monkeypatch) -> None:
    _gridfs_mode(monkeypatch, mongo_reachable=True)
    response = await client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok", "mongodb": "ok"}


async def test_readyz_unavailable_when_mongodb_unreachable(
    client: AsyncClient, monkeypatch
) -> None:
    _gridfs_mode(monkeypatch, mongo_reachable=False)
    response = await client.get("/readyz")
    assert response.status_code == 503
    # Status words only — never a connection string, host or error detail.
    assert response.json() == {"status": "unavailable", "database": "ok", "mongodb": "unreachable"}
    assert "stub" not in response.text


async def test_healthz_does_not_depend_on_mongodb(client: AsyncClient, monkeypatch) -> None:
    _gridfs_mode(monkeypatch, mongo_reachable=False)
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
