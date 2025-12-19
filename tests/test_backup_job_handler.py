import base64
import os
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from dateutil import tz
from kubernetes.client.rest import ApiException

# Ensure src is importable when running pytest from repo root
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from handlers.backup_job_handler import OdooBackupJobHandler  # noqa: E402


@pytest.fixture(autouse=True)
def db_env(monkeypatch):
    monkeypatch.setenv("DB_HOST", "postgres.example")
    monkeypatch.setenv("DB_PORT", "5432")
    monkeypatch.setenv("DEFAULT_ODOO_IMAGE", "odoo:18.0")


@pytest.fixture(autouse=True)
def mock_job_create(monkeypatch):
    # Avoid live API calls; just return the Job body
    monkeypatch.setattr(
        "handlers.backup_job_handler.client.BatchV1Api.create_namespaced_job",
        lambda self, namespace, body: body,
    )


def _make_odoo_instance(image="registry/odoo:custom", pull_secret="pull-me"):
    return {
        "metadata": {
            "name": "demo",
            "uid": "1234-5678",
        },
        "spec": {
            **({"image": image} if image is not None else {}),
            **({"imagePullSecret": pull_secret} if pull_secret is not None else {}),
        },
    }


def _make_job_body(destination=None, format="zip", with_filestore=True, status=None):
    destination = destination or {"bucket": "b", "objectKey": "demo/backup.dump"}
    return {
        "metadata": {"name": "backup1", "namespace": "default", "uid": "u1"},
        "spec": {
            "odooInstanceRef": {"name": "demo", "namespace": "default"},
            "destination": destination,
            "format": format,
            "withFilestore": with_filestore,
        },
        **({"status": status} if status is not None else {}),
    }


def test_backup_job_env_and_ports_without_s3(monkeypatch):
    body = _make_job_body()
    handler = OdooBackupJobHandler(body)
    job = handler._create_backup_job(_make_odoo_instance())

    init = job.spec.template.spec.init_containers[0]
    uploader = job.spec.template.spec.containers[0]

    assert init.image == "registry/odoo:custom"
    assert uploader.image == os.environ.get(
        "BACKUP_UPLOAD_IMAGE", "quay.io/minio/mc:latest"
    )

    env = {e.name: e for e in init.env}
    assert env["HOST"].value == "postgres.example"
    assert env["PORT"].value == "5432"
    assert env["USER"].value_from.secret_key_ref.name == "demo-odoo-user"
    assert env["PASSWORD"].value_from.secret_key_ref.name == "demo-odoo-user"
    assert env["DB_NAME"].value == "odoo_1234_5678"
    assert env["BACKUP_FORMAT"].value == "zip"
    assert env["BACKUP_WITH_FILESTORE"].value == "True"
    assert env["LOCAL_FILENAME"].value == "backup.dump"

    # No S3 envs when s3CredentialsSecretRef absent
    assert "AWS_ACCESS_KEY_ID" not in env
    assert "AWS_SECRET_ACCESS_KEY" not in env

    # Pull secret forwarded
    pull_secrets = job.spec.template.spec.image_pull_secrets
    assert pull_secrets[0].name == "pull-me"


def test_backup_job_with_s3_credentials(monkeypatch):
    access = base64.b64encode(b"AK").decode()
    secret = base64.b64encode(b"SK").decode()

    def fake_read_secret(name, namespace):
        assert name == "s3-creds"
        return SimpleNamespace(data={"accessKey": access, "secretKey": secret})

    monkeypatch.setattr(
        "handlers.backup_job_handler.client.CoreV1Api.read_namespaced_secret",
        lambda self, name, namespace: fake_read_secret(name, namespace),
    )

    dest = {
        "bucket": "b",
        "objectKey": "path/file.dump",
        "s3CredentialsSecretRef": {"name": "s3-creds", "namespace": "default"},
    }
    body = _make_job_body(destination=dest, format="dump", with_filestore=False)
    handler = OdooBackupJobHandler(body)
    job = handler._create_backup_job(_make_odoo_instance(image=None, pull_secret=None))

    init = job.spec.template.spec.init_containers[0]
    env = {e.name: e for e in init.env}
    assert env["AWS_ACCESS_KEY_ID"].value == "AK"
    assert env["AWS_SECRET_ACCESS_KEY"].value == "SK"
    # Format and filestore flags
    assert env["BACKUP_FORMAT"].value == "dump"
    assert env["BACKUP_WITH_FILESTORE"].value == "False"

    # Default image fallback when instance spec lacks image
    assert init.image == "odoo:18.0"


def test_on_create_missing_instance(monkeypatch):
    body = _make_job_body()
    handler = OdooBackupJobHandler(body)
    status_calls = []

    monkeypatch.setattr(
        "handlers.backup_job_handler.client.CustomObjectsApi.get_namespaced_custom_object",
        lambda *args, **kwargs: (_ for _ in ()).throw(ApiException(status=404)),
    )
    monkeypatch.setattr(
        "handlers.backup_job_handler.OdooBackupJobHandler._update_status",
        lambda self, phase, **kwargs: status_calls.append((phase, kwargs)),
    )
    handler.on_create()
    assert status_calls and status_calls[-1][0] == "Failed"


def test_on_create_skips_terminal(monkeypatch):
    body = _make_job_body(status={"phase": "Completed"})
    handler = OdooBackupJobHandler(body)
    calls = []
    monkeypatch.setattr(
        "handlers.backup_job_handler.client.CustomObjectsApi.get_namespaced_custom_object",
        lambda *args, **kwargs: calls.append(True),
    )
    handler.on_create()
    assert calls == []


def test_check_job_status_success(monkeypatch):
    body = _make_job_body(status={"jobName": "job-1"})
    handler = OdooBackupJobHandler(body)
    job_obj = SimpleNamespace(
        status=SimpleNamespace(
            succeeded=1,
            failed=None,
            completion_time=datetime(2025, 1, 2, tzinfo=tz.tzutc()),
        )
    )
    monkeypatch.setattr(
        "handlers.backup_job_handler.client.BatchV1Api.read_namespaced_job",
        lambda self, name, namespace: job_obj,
    )
    status_calls = []
    notify_calls = []
    monkeypatch.setattr(
        "handlers.backup_job_handler.OdooBackupJobHandler._update_status",
        lambda self, phase, **kwargs: status_calls.append((phase, kwargs)),
    )
    monkeypatch.setattr(
        "handlers.backup_job_handler.OdooBackupJobHandler._notify_webhook",
        lambda self, phase: notify_calls.append(phase),
    )
    handler.check_job_status()
    assert status_calls and status_calls[-1][0] == "Completed"
    assert notify_calls == ["Completed"]


def test_check_job_status_failed(monkeypatch):
    body = _make_job_body(status={"jobName": "job-1"})
    handler = OdooBackupJobHandler(body)
    job_obj = SimpleNamespace(
        status=SimpleNamespace(
            succeeded=None,
            failed=1,
            completion_time=datetime(2025, 1, 2, tzinfo=tz.tzutc()),
        )
    )
    monkeypatch.setattr(
        "handlers.backup_job_handler.client.BatchV1Api.read_namespaced_job",
        lambda self, name, namespace: job_obj,
    )
    status_calls = []
    notify_calls = []
    monkeypatch.setattr(
        "handlers.backup_job_handler.OdooBackupJobHandler._update_status",
        lambda self, phase, **kwargs: status_calls.append((phase, kwargs)),
    )
    monkeypatch.setattr(
        "handlers.backup_job_handler.OdooBackupJobHandler._notify_webhook",
        lambda self, phase: notify_calls.append(phase),
    )
    handler.check_job_status()
    assert status_calls and status_calls[-1][0] == "Failed"
    assert notify_calls == ["Failed"]


def test_notify_webhook_direct_token(monkeypatch):
    body = _make_job_body()
    handler = OdooBackupJobHandler(body)
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
    assert posted["headers"]["Authorization"] == "Bearer abc"
    assert posted["json"]["phase"] == "Completed"


def test_notify_webhook_secret_token(monkeypatch):
    body = _make_job_body()
    handler = OdooBackupJobHandler(body)
    handler.webhook = {
        "url": "https://example.com/hook",
        "secretTokenSecretRef": {"name": "hooksec", "key": "token"},
    }

    def fake_read_secret(self, name, namespace):
        assert name == "hooksec"
        return SimpleNamespace(data={"token": base64.b64encode(b"sek").decode()})

    posted = {}
    monkeypatch.setattr(
        "handlers.backup_job_handler.client.CoreV1Api.read_namespaced_secret",
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
    assert posted["headers"].get("Authorization") == "Bearer sek"
    assert posted["json"]["phase"] == "Completed"


def test_update_status_camel_case(monkeypatch):
    body = _make_job_body()
    handler = OdooBackupJobHandler(body)
    calls = []

    monkeypatch.setattr(
        "handlers.backup_job_handler.client.CustomObjectsApi.patch_namespaced_custom_object_status",
        lambda *args, **kwargs: calls.append(kwargs["body"]),
    )

    handler._update_status(
        "Completed",
        job_name="job-1",
        start_time="t1",
        completion_time="t2",
        message="done",
    )
    status = calls[-1]["status"]
    assert status["phase"] == "Completed"
    assert status["jobName"] == "job-1"
    assert status["startTime"] == "t1"
    assert status["completionTime"] == "t2"
    assert status["message"] == "done"
