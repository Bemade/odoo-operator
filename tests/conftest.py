import types
import pytest


@pytest.fixture
def minimal_handler():
    """Provide a minimal fake OdooHandler-like object."""
    handler = types.SimpleNamespace()
    handler.operator_ns = "odoo-operator"
    handler.tls_cert = types.SimpleNamespace(
        resource={"metadata": {"name": "dummy-tls"}}
    )
    handler.spec = {}
    handler.namespace = "default"
    handler.owner_reference = {"fake": "owner"}
    handler.name = "test"
    handler.defaults = {}
    return handler
