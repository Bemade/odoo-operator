import sys
from pathlib import Path
from types import SimpleNamespace


# Ensure src is importable when running pytest from repo root
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from handlers.tls_cert import TLSCert  # noqa: E402


def _make_handler(name="test", hosts=None, issuer="letsencrypt"):
    hosts = hosts or ["example.com", "www.example.com"]
    return SimpleNamespace(
        name=name,
        namespace="default",
        spec={"ingress": {"hosts": hosts, "issuer": issuer}},
        owner_reference={"fake": "owner"},
    )


def test_tls_cert_names_and_dns():
    handler = _make_handler(name="myodoo", hosts=["odoo.example.com"])
    cert = TLSCert(handler)._get_resource_body()  # noqa: SLF001 private use OK in tests

    assert cert["metadata"].name == "odoo.example.com-cert"
    assert cert["spec"]["secretName"] == "odoo.example.com-cert"
    assert cert["spec"]["dnsNames"] == ["odoo.example.com"]
    assert cert["spec"]["issuerRef"]["name"] == "letsencrypt"
    assert cert["spec"]["issuerRef"]["kind"] == "ClusterIssuer"


def test_tls_cert_owner_reference():
    owner_ref = {"name": "owner", "uid": "123"}
    handler = _make_handler()
    handler.owner_reference = owner_ref
    cert = TLSCert(handler)._get_resource_body()
    assert cert["metadata"].owner_references == [owner_ref]
