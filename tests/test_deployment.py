import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# Ensure src is importable when running pytest from repo root
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from handlers.deployment import Deployment  # noqa: E402


def _make_handler(spec=None, defaults=None, name="test"):
    return SimpleNamespace(
        name=name,
        namespace="default",
        spec=spec or {},
        defaults=defaults or {},
        owner_reference={"fake": "owner"},
        odoo_user_secret=None,
    )


@pytest.fixture(autouse=True)
def db_env(monkeypatch):
    monkeypatch.setenv("DB_HOST", "postgres.example")
    monkeypatch.setenv("DB_PORT", "5432")


def test_deployment_defaults_and_ports():
    handler = _make_handler(defaults={"odooImage": "odoo:18.0"})
    dep_handler = Deployment(handler)
    dep_handler._resource = SimpleNamespace(
        spec=None
    )  # avoid live API call for replicas
    dep = dep_handler._get_resource_body()  # noqa: SLF001 private use OK in tests
    tpl = dep.spec.template
    container = tpl.spec.containers[0]

    assert container.image == "odoo:18.0"
    assert container.command == ["/entrypoint.sh", "odoo"]

    ports = {p.name: p.container_port for p in container.ports}
    assert ports == {"http": 8069, "websocket": 8072}

    assert tpl.spec.security_context.run_as_user == 100
    assert tpl.spec.security_context.run_as_group == 101
    assert tpl.spec.security_context.fs_group == 101

    mounts = {m.name: m.mount_path for m in container.volume_mounts}
    assert mounts == {"filestore": "/var/lib/odoo", "odoo-conf": "/etc/odoo"}

    volumes = {v.name for v in tpl.spec.volumes}
    assert volumes == {"filestore", "odoo-conf"}

    # Probes target the health endpoint on 8069
    assert container.liveness_probe.http_get.port == 8069
    assert container.liveness_probe.http_get.path == "/web/health"
    assert container.readiness_probe.http_get.port == 8069
    assert container.readiness_probe.http_get.path == "/web/health"


def test_deployment_image_override_and_pull_secret():
    spec = {"image": "registry/odoo:custom", "imagePullSecret": "pull-me"}
    handler = _make_handler(spec=spec, defaults={"odooImage": "odoo:18.0"})
    dep_handler = Deployment(handler)
    dep_handler._resource = SimpleNamespace(spec=None)
    dep = dep_handler._get_resource_body()
    container = dep.spec.template.spec.containers[0]

    assert container.image == "registry/odoo:custom"
    assert dep.spec.template.spec.image_pull_secrets[0].name == "pull-me"


def test_deployment_env_vars_from_secrets_and_env(monkeypatch):
    handler = _make_handler(defaults={"odooImage": "odoo:18.0"}, name="demo")
    dep_handler = Deployment(handler)
    dep_handler._resource = SimpleNamespace(spec=None)
    dep = dep_handler._get_resource_body()
    envs = {e.name: e for e in dep.spec.template.spec.containers[0].env}

    assert envs["HOST"].value == "postgres.example"
    assert envs["PORT"].value == "5432"
    assert envs["USER"].value_from.secret_key_ref.name == "demo-odoo-user"
    assert envs["USER"].value_from.secret_key_ref.key == "username"
    assert envs["PASSWORD"].value_from.secret_key_ref.name == "demo-odoo-user"
    assert envs["PASSWORD"].value_from.secret_key_ref.key == "password"
