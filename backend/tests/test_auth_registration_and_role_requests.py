from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app import models  # noqa: F401
from app.core.config import settings
from app.db.base import Base
from app.main import app


def _assert_safe_test_database() -> None:
    url = make_url(settings.database_url)
    if settings.env != "test" or url.host != "postgres-tests" or url.database != "gauges_test":
        raise AssertionError(
            "Refusing to reset database outside the isolated test database. "
            f"APP_ENV={settings.env!r}, host={url.host!r}, database={url.database!r}"
        )


async def _reset_db() -> None:
    _assert_safe_test_database()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _login(client: TestClient, *, email: str, password: str) -> dict:
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_registration_and_admin_request_full_flow() -> None:
    asyncio.run(_reset_db())
    with TestClient(app) as client:
        reg = client.post("/api/v1/auth/register", json={"email": "candidate@example.com", "password": "secret123"})
        assert reg.status_code == 201, reg.text
        assert reg.json()["user"]["role"] == "viewer"
        viewer_token = reg.json()["access_token"]

        duplicate_reg = client.post("/api/v1/auth/register", json={"email": "candidate@example.com", "password": "secret123"})
        assert duplicate_reg.status_code == 409
        assert duplicate_reg.json()["detail"] == "Пользователь с такой почтой уже зарегистрирован"

        create_req = client.post("/api/v1/admin-role-requests", headers=_auth_headers(viewer_token))
        assert create_req.status_code == 201, create_req.text
        request_id = create_req.json()["id"]

        duplicate_req = client.post("/api/v1/admin-role-requests", headers=_auth_headers(viewer_token))
        assert duplicate_req.status_code == 409
        assert duplicate_req.json()["detail"] == "Заявка уже отправлена и ожидает рассмотрения"

        list_attempt = client.get("/api/v1/admin-role-requests", headers=_auth_headers(viewer_token))
        assert list_attempt.status_code == 403

        reject_attempt = client.post(
            f"/api/v1/admin-role-requests/{request_id}/reject",
            headers=_auth_headers(viewer_token),
            json={"review_comment": "no"},
        )
        assert reject_attempt.status_code == 403

        admin_login = _login(client, email="admin@example.com", password="admin123")
        admin_token = admin_login["access_token"]

        listed = client.get("/api/v1/admin-role-requests", headers=_auth_headers(admin_token))
        assert listed.status_code == 200, listed.text
        assert any(item["id"] == request_id and item["status"] == "pending" for item in listed.json())

        approved = client.post(
            f"/api/v1/admin-role-requests/{request_id}/approve",
            headers=_auth_headers(admin_token),
            json={"review_comment": "approved"},
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["status"] == "approved"

        viewer_login = _login(client, email="candidate@example.com", password="secret123")
        assert viewer_login["user"]["role"] == "admin"
