import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# Ensure src is importable when running pytest from repo root
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from handlers.pull_secret import PullSecret  # noqa: E402


def _make_handler(secret_name="regcred"):
    return SimpleNamespace(
        name="demo",
        spec={"imagePullSecret": secret_name},
        namespace="default",
        operator_ns="odoo-operator",
        owner_reference={"fake": "owner"},
    )


def test_pull_secret_skips_when_missing(monkeypatch):
    handler = _make_handler(secret_name=None)
    calls = []
    from kubernetes.client.rest import ApiException

    # Ensure resource lookup returns 404 (so resource is treated as missing) but no create is invoked
    monkeypatch.setattr(
        "handlers.pull_secret.client.CoreV1Api.read_namespaced_secret",
        lambda self, name, namespace: (_ for _ in ()).throw(ApiException(status=404)),
    )
    monkeypatch.setattr(
        "handlers.pull_secret.client.CoreV1Api.create_namespaced_secret",
        lambda self, namespace, body: calls.append((namespace, body)),
    )
    PullSecret(handler).handle_create()
    assert calls == []


def test_pull_secret_copies_data(monkeypatch):
    handler = _make_handler(secret_name="regcred")

    def fake_read_secret(self, name, namespace):
        from kubernetes.client.rest import ApiException

        assert name == "regcred"
        # Simulate missing in target namespace so creation happens; then actual read in operator_ns
        if namespace == "default":
            raise ApiException(status=404)
        return SimpleNamespace(data={"auth": "Zm9vOmJhcg=="})

    created = {}

    def fake_create(self, namespace, body):
        created["namespace"] = namespace
        created["body"] = body
        return body

    monkeypatch.setattr(
        "handlers.pull_secret.client.CoreV1Api.read_namespaced_secret", fake_read_secret
    )
    monkeypatch.setattr(
        "handlers.pull_secret.client.CoreV1Api.create_namespaced_secret", fake_create
    )

    PullSecret(handler).handle_create()

    assert created["namespace"] == "default"
    body = created["body"]
    assert body.metadata.name == "regcred"
    assert body.metadata.owner_references == [{"fake": "owner"}]
    assert body.type == "kubernetes.io/dockerconfigjson"
    assert body.data == {"auth": "Zm9vOmJhcg=="}
