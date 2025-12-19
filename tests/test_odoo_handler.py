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

from handlers.odoo_handler import OdooHandler  # noqa: E402


@pytest.fixture(autouse=True)
def stub_subhandlers(monkeypatch):
    class DummyHandler:
        def __init__(self, handler):
            self.handler = handler
            self.name = getattr(handler, "name", "demo")
            self.namespace = getattr(handler, "namespace", "default")
            self.owner_reference = {"fake": "owner"}

        def handle_create(self):
            pass

        def handle_update(self):
            pass

        def handle_delete(self):
            pass

    # Stub out all subhandler classes to avoid ingress/tls field access and API calls
    for cls_name in [
        "PullSecret",
        "OdooUserSecret",
        "FilestorePVC",
        "OdooConf",
        "TLSCert",
        "Deployment",
        "Service",
        "Ingress",
    ]:
        monkeypatch.setattr(f"handlers.odoo_handler.{cls_name}", DummyHandler)


def _make_body():
    return {
        "metadata": {"name": "demo", "namespace": "default", "uid": "u1"},
        "spec": {"webhook": {"url": "https://example.com/hook"}},
    }


def test_on_create_runs_handlers_and_status(monkeypatch):
    body = _make_body()
    handler = OdooHandler(body)

    create_calls = []
    fake_handlers = [
        SimpleNamespace(
            handle_create=(lambda i=i: create_calls.append(i)),
            handle_update=lambda: None,
        )
        for i in range(3)
    ]
    handler.handlers = fake_handlers

    status_calls = []
    monkeypatch.setattr(
        "handlers.odoo_handler.client.CustomObjectsApi.patch_namespaced_custom_object_status",
        lambda *args, **kwargs: status_calls.append(kwargs["body"]),
    )
    monkeypatch.setattr(
        "handlers.odoo_handler.OdooHandler._call_webhook",
        lambda self, phase, message="": status_calls.append({"webhook": phase}),
    )

    handler.on_create()
    assert create_calls == [0, 1, 2]
    assert any(b["status"]["phase"] == "Running" for b in status_calls if "status" in b)


def test_on_update_runs_handlers(monkeypatch):
    body = _make_body()
    handler = OdooHandler(body)
    update_calls = []
    handler.handlers = [
        SimpleNamespace(handle_update=(lambda i=i: update_calls.append(i)))
        for i in range(2)
    ]
    handler.on_update()
    assert update_calls == [0, 1]


def test_call_webhook_posts(monkeypatch):
    body = _make_body()
    handler = OdooHandler(body)
    posted = {}
    monkeypatch.setattr(
        "handlers.odoo_handler.requests.post",
        lambda url, json, timeout, verify: posted.update({"url": url, "json": json}),
    )
    handler._call_webhook("Running", "ok")
    assert posted["url"] == "https://example.com/hook"
    assert posted["json"]["phase"] == "Running"
    assert posted["json"]["message"] == "ok"


def test_call_webhook_no_url(monkeypatch):
    body = {
        "metadata": {"name": "demo", "namespace": "default", "uid": "u1"},
        "spec": {},
    }
    handler = OdooHandler(body)
    monkeypatch.setattr(
        "handlers.odoo_handler.requests.post",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("should not post")),
    )
    handler._call_webhook("Running")


def test_validate_database_exists_success(monkeypatch):
    body = _make_body()
    handler = OdooHandler(body)
    handler.odoo_user_secret = SimpleNamespace(username="odoo-user")

    class FakeCursor:
        def __init__(self):
            self.calls = 0

        def execute(self, sql, params=None):
            self.calls += 1

        def fetchone(self):
            if self.calls == 1:
                return True  # database exists
            return True  # owned by user

        def close(self):
            pass

    class FakeConn:
        def __init__(self):
            self.autocommit = False

        def cursor(self):
            return FakeCursor()

        def close(self):
            pass

    monkeypatch.setattr("psycopg2.connect", lambda **kwargs: FakeConn())
    exists, err = handler.validate_database_exists("db1")
    assert exists is True
    assert err is None


def test_validate_database_exists_no_user():
    body = _make_body()
    handler = OdooHandler(body)
    handler.odoo_user_secret = SimpleNamespace(username=None)
    exists, err = handler.validate_database_exists("db1")
    assert exists is False
    assert "username" in err


def test_from_job_info_404(monkeypatch):
    monkeypatch.setattr(
        "handlers.odoo_handler.client.CustomObjectsApi.get_namespaced_custom_object",
        lambda *args, **kwargs: (_ for _ in ()).throw(ApiException(status=404)),
    )
    assert OdooHandler.from_job_info("default", "demo") is None
