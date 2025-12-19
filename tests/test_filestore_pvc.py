import sys
from pathlib import Path
from types import SimpleNamespace


# Ensure src is importable when running pytest from repo root
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from handlers.filestore_pvc import FilestorePVC  # noqa: E402


def _make_handler(spec=None, defaults=None, name="demo"):
    return SimpleNamespace(
        name=name,
        namespace="default",
        spec=spec or {},
        defaults=defaults or {},
        owner_reference={"fake": "owner"},
    )


def test_filestore_pvc_defaults():
    handler = _make_handler(defaults={"filestoreSize": "5Gi"})
    pvc = FilestorePVC(
        handler
    )._get_resource_body()

    assert pvc.metadata.name == "demo-filestore-pvc"
    assert pvc.spec.resources.requests["storage"] == "5Gi"
    assert pvc.spec.storage_class_name is None


def test_filestore_pvc_overrides():
    spec = {"filestore": {"storageSize": "10Gi", "storageClass": "fast-ssd"}}
    handler = _make_handler(spec=spec, defaults={"filestoreSize": "5Gi"})
    pvc = FilestorePVC(handler)._get_resource_body()

    assert pvc.spec.resources.requests["storage"] == "10Gi"
    assert pvc.spec.storage_class_name == "fast-ssd"
