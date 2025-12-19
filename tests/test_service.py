import sys
from pathlib import Path
from types import SimpleNamespace


# Ensure src is importable when running pytest from repo root
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from handlers.service import Service  # noqa: E402


def _make_handler(name="test"):
    return SimpleNamespace(
        name=name,
        namespace="default",
        spec={},
        owner_reference={"fake": "owner"},
    )


def test_service_ports_and_selector():
    handler = _make_handler()
    svc = Service(handler)._get_resource_body()  # noqa: SLF001 private use OK in tests
    ports = svc.spec.ports
    assert len(ports) == 2
    assert ports[0].name == "http"
    assert ports[0].port == 8069
    assert ports[0].target_port == 8069
    assert ports[1].name == "websocket"
    assert ports[1].port == 8072
    assert ports[1].target_port == 8072
    assert svc.spec.selector == {"app": handler.name}
    assert svc.spec.type == "ClusterIP"


def test_service_metadata_labels():
    handler = _make_handler(name="my-odoo")
    svc = Service(handler)._get_resource_body()
    assert svc.metadata.labels == {"app": "my-odoo"}
    assert svc.metadata.name == "my-odoo"
