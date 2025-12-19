from types import SimpleNamespace

from kubernetes import client
import pytest

# PVCHandler lives under handlers.pvc_handler
from handlers.pvc_handler import PVCHandler


class DummyPVC(PVCHandler):
    def __init__(self, handler, **kwargs):
        super().__init__(handler, pvc_name_suffix="data", default_size="10Gi", **kwargs)

    def _get_resource_body(self):
        return client.V1PersistentVolumeClaim(
            metadata=client.V1ObjectMeta(
                name=self._get_pvc_name(), namespace=self.namespace
            ),
            spec=client.V1PersistentVolumeClaimSpec(
                access_modes=["ReadWriteOnce"],
                resources=client.V1ResourceRequirements(
                    requests={"storage": self.default_size}
                ),
            ),
        )


@pytest.fixture
def handler():
    return SimpleNamespace(
        name="demo",
        namespace="default",
        spec={"storage": {"size": "50Gi"}},
        status={},
        body={"metadata": {"name": "demo", "namespace": "default"}},
        owner_reference={"fake": "owner"},
    )


def test_handle_create_creates_pvc(monkeypatch, handler):
    pvc_calls = {}

    monkeypatch.setattr(
        "handlers.pvc_handler.PVCHandler._read_resource", lambda self: None
    )
    monkeypatch.setattr(
        "handlers.pvc_handler.client.CoreV1Api.create_namespaced_persistent_volume_claim",
        lambda self, namespace, body: pvc_calls.setdefault("body", body),
    )
    dummy = DummyPVC(handler)
    dummy.handle_create()
    body = pvc_calls["body"]
    assert body.metadata.name == "demo-data"
    assert body.spec.resources.requests["storage"] == "10Gi"


def test_handle_update_patches(monkeypatch, handler):
    patch_calls = {}
    monkeypatch.setattr(
        "handlers.pvc_handler.PVCHandler._read_resource", lambda self: SimpleNamespace()
    )
    monkeypatch.setattr(
        "handlers.pvc_handler.client.CoreV1Api.patch_namespaced_persistent_volume_claim",
        lambda self, name, namespace, body: patch_calls.setdefault("body", body),
    )
    dummy = DummyPVC(handler)
    dummy.handle_update()
    assert patch_calls["body"].metadata.name == "demo-data"


def test_get_storage_size_with_spec_path(handler):
    # avoid network during __init__
    DummyPVC._read_resource = lambda self: None  # type: ignore
    dummy = DummyPVC(handler)
    size = dummy._get_storage_size(spec_path=["storage", "size"])
    assert size == "50Gi"


def test_read_resource_404_returns_none(monkeypatch, handler):
    dummy = DummyPVC(handler)
    monkeypatch.setattr(
        "handlers.pvc_handler.client.CoreV1Api.read_namespaced_persistent_volume_claim",
        lambda self, name, namespace: (_ for _ in ()).throw(
            client.ApiException(status=404)
        ),
    )
    assert dummy._read_resource() is None
