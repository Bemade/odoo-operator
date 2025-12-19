import os
import base64
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from kubernetes import client
from kubernetes.client.rest import ApiException

# Ensure src is importable when running pytest from repo root
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from handlers.init_job_handler import OdooInitJobHandler  # noqa: E402


@pytest.fixture(autouse=True)
def db_env(monkeypatch):
    monkeypatch.setenv("DB_HOST", "postgres.example")
    monkeypatch.setenv("DB_PORT", "5432")
    monkeypatch.setenv("DEFAULT_ODOO_IMAGE", "odoo:18.0")


def _make_body(status=None, modules=None):
    return {
        "metadata": {"name": "init1", "namespace": "default", "uid": "u1"},
        "spec": {
            "odooInstanceRef": {"name": "demo", "namespace": "default"},
            **({"modules": modules} if modules is not None else {}),
        },
        "status": status or {},
    }


def _make_instance(status_phase=None, image="odoo:custom", pull_secret=None):
    spec = {"image": image}
    if pull_secret:
        spec["imagePullSecret"] = pull_secret
    return {
        "metadata": {"name": "demo", "uid": "1234-5678"},
        "spec": spec,
        "status": {"phase": status_phase} if status_phase else {},
    }


def test_on_create_builds_job_and_updates_status(monkeypatch):
    body = _make_body(modules=["base", "sale"])
    handler = OdooInitJobHandler(body)

    # Mock instance fetch
    monkeypatch.setattr(
        "handlers.init_job_handler.client.CustomObjectsApi.get_namespaced_custom_object",
        lambda *args, **kwargs: _make_instance(
            image="registry/odoo:custom", pull_secret="pullme"
        ),
    )

    # Capture scale and status calls
    scale_calls = []
    status_calls = []
    monkeypatch.setattr(
        "handlers.init_job_handler.OdooInitJobHandler._scale_deployment",
        lambda self, name, ns, replicas: scale_calls.append((name, ns, replicas)),
    )
    monkeypatch.setattr(
        "handlers.init_job_handler.OdooInitJobHandler._update_status",
        lambda self, phase, **kwargs: status_calls.append((phase, kwargs)),
    )

    # Mock job creation
    creation_time = datetime(2025, 1, 1, tzinfo=timezone.utc)

    def fake_create(self, namespace, body):
        # Return the same body so spec/template are preserved; set name/timestamp
        body.metadata = body.metadata or client.V1ObjectMeta()
        body.metadata.name = "job-abc"
        body.metadata.creation_timestamp = creation_time
        return body

    monkeypatch.setattr(
        "handlers.init_job_handler.client.BatchV1Api.create_namespaced_job", fake_create
    )

    handler.on_create()

    # Scale down before init
    assert scale_calls == [("demo", "default", 0)]
    # Status updated to Running with job name and start_time
    assert status_calls and status_calls[-1][0] == "Running"
    assert status_calls[-1][1]["job_name"] == "job-abc"
    assert "start_time" in status_calls[-1][1]

    # Inspect job spec that was built
    job = handler._create_init_job(
        _make_instance(image="registry/odoo:custom", pull_secret="pullme")
    )
    init_container = job.spec.template.spec.containers[0]
    assert init_container.image == "registry/odoo:custom"
    args = init_container.args
    assert "-i" in args and "base,sale" in args
    env = {e.name: e for e in init_container.env}
    assert env["HOST"].value == "postgres.example"
    assert env["PORT"].value == "5432"
    assert env["USER"].value_from.secret_key_ref.name == "demo-odoo-user"
    assert env["PASSWORD"].value_from.secret_key_ref.name == "demo-odoo-user"
    mounts = {m.name: m.mount_path for m in init_container.volume_mounts}
    assert mounts == {"filestore": "/var/lib/odoo", "odoo-conf": "/etc/odoo"}


def test_on_create_skips_busy_instance(monkeypatch):
    body = _make_body()
    handler = OdooInitJobHandler(body)

    monkeypatch.setattr(
        "handlers.init_job_handler.client.CustomObjectsApi.get_namespaced_custom_object",
        lambda *args, **kwargs: _make_instance(status_phase="Upgrading"),
    )
    status_calls = []
    monkeypatch.setattr(
        "handlers.init_job_handler.OdooInitJobHandler._update_status",
        lambda self, phase, **kwargs: status_calls.append((phase, kwargs)),
    )
    handler.on_create()
    assert status_calls and status_calls[-1][0] == "Failed"
    assert "already Upgrading" in status_calls[-1][1]["message"]


def test_on_create_missing_instance(monkeypatch):
    body = _make_body()
    handler = OdooInitJobHandler(body)

    def fake_get(*args, **kwargs):
        raise ApiException(status=404)

    monkeypatch.setattr(
        "handlers.init_job_handler.client.CustomObjectsApi.get_namespaced_custom_object",
        fake_get,
    )
    status_calls = []
    monkeypatch.setattr(
        "handlers.init_job_handler.OdooInitJobHandler._update_status",
        lambda self, phase, **kwargs: status_calls.append((phase, kwargs)),
    )
    handler.on_create()
    assert status_calls and status_calls[-1][0] == "Failed"
    assert "not found" in status_calls[-1][1]["message"]


def test_on_create_skips_when_already_running(monkeypatch):
    body = _make_body(status={"phase": "Running"})
    handler = OdooInitJobHandler(body)
    calls = []
    monkeypatch.setattr(
        "handlers.init_job_handler.client.CustomObjectsApi.get_namespaced_custom_object",
        lambda *args, **kwargs: calls.append(True),
    )
    handler.on_create()
    assert calls == []


def test_check_job_status_completed(monkeypatch):
    body = _make_body(status={"jobName": "job-1"})
    handler = OdooInitJobHandler(body)

    # Fake job status succeeded with completion time
    job_obj = SimpleNamespace(
        status=SimpleNamespace(
            succeeded=1,
            failed=None,
            completion_time=datetime(2025, 1, 2, tzinfo=timezone.utc),
        )
    )
    monkeypatch.setattr(
        "handlers.init_job_handler.client.BatchV1Api.read_namespaced_job",
        lambda self, name, namespace: job_obj,
    )
    status_calls = []
    scale_calls = []
    notify_calls = []
    monkeypatch.setattr(
        "handlers.init_job_handler.OdooInitJobHandler._update_status",
        lambda self, phase, **kwargs: status_calls.append((phase, kwargs)),
    )
    monkeypatch.setattr(
        "handlers.init_job_handler.OdooInitJobHandler._scale_instance_back_up",
        lambda self: scale_calls.append(True),
    )
    monkeypatch.setattr(
        "handlers.init_job_handler.OdooInitJobHandler._notify_webhook",
        lambda self, phase: notify_calls.append(phase),
    )

    handler.check_job_status()
    assert status_calls and status_calls[-1][0] == "Completed"
    assert scale_calls == [True]
    assert notify_calls == ["Completed"]


def test_check_job_status_failed(monkeypatch):
    body = _make_body(status={"jobName": "job-1"})
    handler = OdooInitJobHandler(body)
    job_obj = SimpleNamespace(
        status=SimpleNamespace(
            succeeded=None,
            failed=1,
            completion_time=datetime(2025, 1, 2, tzinfo=timezone.utc),
        )
    )
    monkeypatch.setattr(
        "handlers.init_job_handler.client.BatchV1Api.read_namespaced_job",
        lambda self, name, namespace: job_obj,
    )
    status_calls = []
    notify_calls = []
    scale_calls = []
    monkeypatch.setattr(
        "handlers.init_job_handler.OdooInitJobHandler._update_status",
        lambda self, phase, **kwargs: status_calls.append((phase, kwargs)),
    )
    monkeypatch.setattr(
        "handlers.init_job_handler.OdooInitJobHandler._notify_webhook",
        lambda self, phase: notify_calls.append(phase),
    )
    monkeypatch.setattr(
        "handlers.init_job_handler.OdooInitJobHandler._scale_instance_back_up",
        lambda self: scale_calls.append(True),
    )
    handler.check_job_status()
    assert status_calls and status_calls[-1][0] == "Failed"
    assert notify_calls == ["Failed"]
    assert scale_calls == [True]


def test_check_job_status_skips_terminal(monkeypatch):
    body = _make_body(status={"phase": "Completed", "jobName": "job-1"})
    handler = OdooInitJobHandler(body)
    calls = []
    monkeypatch.setattr(
        "handlers.init_job_handler.client.BatchV1Api.read_namespaced_job",
        lambda *args, **kwargs: calls.append(True),
    )
    handler.check_job_status()
    assert calls == []


def test_notify_webhook_direct_token(monkeypatch):
    body = _make_body()
    handler = OdooInitJobHandler(body)
    handler.webhook = {"url": "https://example.com/hook", "token": "abc"}

    posted = {}

    def fake_post(url, json, headers, timeout):
        posted["url"] = url
        posted["json"] = json
        posted["headers"] = headers
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr("requests.post", fake_post)
    handler._notify_webhook("Completed")
    assert posted["url"] == "https://example.com/hook"
    assert posted["json"]["phase"] == "Completed"
    assert posted["headers"]["Authorization"] == "Bearer abc"


def test_notify_webhook_secret_token(monkeypatch):
    body = _make_body()
    handler = OdooInitJobHandler(body)
    handler.webhook = {
        "url": "https://example.com/hook",
        "secretTokenSecretRef": {"name": "hooksec", "key": "token"},
    }

    def fake_read_secret(self, name, namespace):
        assert name == "hooksec"
        return SimpleNamespace(data={"token": base64.b64encode(b"sek").decode()})

    posted = {}
    monkeypatch.setattr(
        "handlers.init_job_handler.client.CoreV1Api.read_namespaced_secret",
        fake_read_secret,
    )
    monkeypatch.setattr(
        "requests.post",
        lambda url, json, headers, timeout: posted.update(
            {"url": url, "json": json, "headers": headers}
        )
        or SimpleNamespace(status_code=200),
    )

    handler._notify_webhook("Completed")
    # Authorization header should be set from secret token
    assert posted["headers"].get("Authorization") == "Bearer sek"
    assert posted["json"]["phase"] == "Completed"


def test_scale_instance_back_up(monkeypatch):
    body = _make_body()
    handler = OdooInitJobHandler(body)
    scale_calls = []

    monkeypatch.setattr(
        "handlers.init_job_handler.client.CustomObjectsApi.get_namespaced_custom_object",
        lambda *args, **kwargs: {"spec": {"replicas": 3}},
    )
    monkeypatch.setattr(
        "handlers.init_job_handler.OdooInitJobHandler._scale_deployment",
        lambda self, name, ns, replicas: scale_calls.append((name, ns, replicas)),
    )

    handler._scale_instance_back_up()
    assert scale_calls == [("demo", "default", 3)]


def test_scale_deployment_404(monkeypatch, caplog):
    body = _make_body()
    handler = OdooInitJobHandler(body)

    def fake_patch(*args, **kwargs):
        raise ApiException(status=404)

    monkeypatch.setattr(
        "handlers.init_job_handler.client.AppsV1Api.patch_namespaced_deployment_scale",
        fake_patch,
    )

    with caplog.at_level("WARNING"):
        handler._scale_deployment("demo", "default", 0)
    assert any("not found" in msg for msg in caplog.messages)
