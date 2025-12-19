import sys
from pathlib import Path

import kubernetes.client as k8s


# Ensure src is importable when running pytest from repo root
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from handlers.ingress import Ingress  # noqa: E402


def test_ingress_class_unset_by_default(minimal_handler):
    """When no spec/default is provided, ingressClassName should be omitted."""
    ingress = Ingress(minimal_handler)
    spec = ingress._build_ingress_spec()  # noqa: SLF001 private use OK in tests
    assert spec.spec.ingress_class_name is None


def test_ingress_class_from_spec(minimal_handler):
    """Spec value should win over defaults and be set on the Ingress spec."""
    minimal_handler.spec = {"ingress": {"hosts": ["example.com"], "class": "traefik"}}
    ingress = Ingress(minimal_handler)
    spec = ingress._build_ingress_spec()
    assert spec.spec.ingress_class_name == "traefik"
    # Verify paths are present and target expected ports
    paths = spec.spec.rules[0].http.paths
    ports = [p.backend.service.port.number for p in paths]
    hosts = [rule.host for rule in spec.spec.rules]
    assert hosts == ["example.com"]
    assert ports == [8072, 8069]


def test_ingress_class_from_defaults(minimal_handler):
    """Fallback to defaults when spec omits class."""
    minimal_handler.spec = {"ingress": {"hosts": ["example.com"]}}
    minimal_handler.defaults = {"ingressClass": "nginx"}
    ingress = Ingress(minimal_handler)
    spec = ingress._build_ingress_spec()
    assert spec.spec.ingress_class_name == "nginx"


def test_ingress_class_prefers_spec_over_defaults(minimal_handler):
    """Spec ingress.class should override defaults ingressClass."""
    minimal_handler.spec = {"ingress": {"hosts": ["example.com"], "class": "traefik"}}
    minimal_handler.defaults = {"ingressClass": "nginx"}
    ingress = Ingress(minimal_handler)
    spec = ingress._build_ingress_spec()
    assert spec.spec.ingress_class_name == "traefik"


def test_ingress_class_empty_string_is_ignored(minimal_handler):
    """Empty string should be treated as unset."""
    minimal_handler.spec = {"ingress": {"hosts": ["example.com"], "class": ""}}
    ingress = Ingress(minimal_handler)
    spec = ingress._build_ingress_spec()
    assert spec.spec.ingress_class_name is None


def test_ingress_handles_non_dict_ingress(minimal_handler):
    """Non-dict ingress config should not crash and should omit class."""
    minimal_handler.spec = {"ingress": True}
    ingress = Ingress(minimal_handler)
    spec = ingress._build_ingress_spec()
    assert spec.spec.ingress_class_name is None
    assert spec.spec.rules == []


def test_ingress_tls_secret_propagates(minimal_handler):
    """TLS secret name should come from tls_cert metadata."""
    minimal_handler.spec = {"ingress": {"hosts": ["example.com"]}}
    ingress = Ingress(minimal_handler)
    spec = ingress._build_ingress_spec()
    assert spec.spec.tls[0].secret_name == "dummy-tls"


def test_ingress_path_order_and_ports(minimal_handler):
    """Ensure websocket path is first and ports are correct."""
    minimal_handler.spec = {"ingress": {"hosts": ["example.com"]}}
    ingress = Ingress(minimal_handler)
    spec = ingress._build_ingress_spec()
    paths = spec.spec.rules[0].http.paths
    assert paths[0].path == "/websocket"
    assert paths[0].backend.service.port.number == 8072
    assert paths[1].path == "/"
    assert paths[1].backend.service.port.number == 8069
