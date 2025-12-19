import base64
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from kubernetes.client.rest import ApiException

# Ensure src is importable when running pytest from repo root
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from handlers.odoo_user_secret import OdooUserSecret  # noqa: E402


@pytest.fixture(autouse=True)
def db_env(monkeypatch):
    monkeypatch.setenv("DB_HOST", "postgres.example")
    monkeypatch.setenv("DB_PORT", "5432")
    monkeypatch.setenv("DB_ADMIN_USER", "admin")
    monkeypatch.setenv("DB_ADMIN_PASSWORD", "adm1npass")


def _fake_conn():
    class FakeCursor:
        def __init__(self, calls):
            self.calls = calls

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql):
            self.calls.append(sql)

        def fetchall(self):
            return []

        def fetchone(self):
            return None

    class FakeConn:
        def __init__(self):
            self.calls = []
            self.autocommit = False

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def cursor(self):
            return FakeCursor(self.calls)

        def close(self):
            pass

    return FakeConn()


def test_handle_create_creates_secret(monkeypatch):
    handler = SimpleNamespace(
        name="demo",
        namespace="default",
        spec={},
        owner_reference={"fake": "owner"},
    )
    secret_calls = {}

    # Avoid DB operations: skip deletion and use fake connection
    monkeypatch.setattr(
        "handlers.odoo_user_secret.psycopg2.connect", lambda **kwargs: _fake_conn()
    )
    monkeypatch.setattr(
        "handlers.odoo_user_secret.OdooUserSecret._delete_odoo_db_user",
        lambda self: None,
    )
    monkeypatch.setattr(
        "handlers.odoo_user_secret.OdooUserSecret._read_resource",
        lambda self: (_ for _ in ()).throw(ApiException(status=404)),
    )

    def fake_create_secret(self, namespace, body):
        secret_calls["namespace"] = namespace
        secret_calls["body"] = body
        return body

    monkeypatch.setattr(
        "handlers.odoo_user_secret.client.CoreV1Api.create_namespaced_secret",
        fake_create_secret,
    )

    ous = OdooUserSecret(handler)
    ous.handle_create()

    body = secret_calls["body"]
    assert secret_calls["namespace"] == "default"
    assert body.metadata.name == "demo-odoo-user"
    assert body.type == "Opaque"
    assert body.string_data["username"].startswith("odoo.default.demo")
    assert len(body.string_data["password"]) > 0


def test_username_password_properties_decode_base64():
    handler = SimpleNamespace(
        name="demo",
        namespace="default",
        spec={},
        owner_reference={"fake": "owner"},
    )
    ous = OdooUserSecret(handler)
    ous._resource = SimpleNamespace(
        data={
            "username": base64.b64encode(b"user").decode(),
            "password": base64.b64encode(b"pass").decode(),
        }
    )
    assert ous.username == "user"
    assert ous.password == "pass"


def test_password_none_when_no_resource(monkeypatch):
    handler = SimpleNamespace(
        name="demo",
        namespace="default",
        spec={},
        owner_reference={"fake": "owner"},
    )
    ous = OdooUserSecret(handler)
    monkeypatch.setattr(
        "handlers.odoo_user_secret.OdooUserSecret._read_resource",
        lambda self: (_ for _ in ()).throw(ApiException(status=404)),
    )
    assert ous.password is None


def test_delete_odoo_db_user(monkeypatch):
    handler = SimpleNamespace(
        name="demo",
        namespace="default",
        spec={},
        owner_reference={"fake": "owner"},
    )

    drop_calls = []

    class FakeCursor:
        def __init__(self):
            self.stage = 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql):
            drop_calls.append(sql)
            # simulate fetchall/fetchone expectations
            self.stage += 1

        def fetchall(self):
            # first fetchall returns empty DB list
            return []

        def fetchone(self):
            # simulate user exists
            return (1,)

    class FakeConn:
        def __init__(self):
            self.autocommit = False

        def cursor(self):
            return FakeCursor()

        def close(self):
            pass

    monkeypatch.setattr(
        "handlers.odoo_user_secret.psycopg2.connect", lambda **kwargs: FakeConn()
    )

    ous = OdooUserSecret(handler)
    # should not raise
    ous._delete_odoo_db_user()
    # We expect at least two executes: list DBs and check role existence; then drop role
    assert any("SELECT datname" in sql for sql in drop_calls)
    assert any("SELECT 1 FROM pg_roles" in sql for sql in drop_calls)
