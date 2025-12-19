import base64
import sys
from pathlib import Path


# Ensure src is importable when running pytest from repo root
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from handlers.odoo_conf import OdooConf  # noqa: E402


def _make_handler(admin_password=None, extra_config=None):
    class DummySecret:
        def __init__(self):
            from types import SimpleNamespace

            self.resource = SimpleNamespace(
                data={"username": base64.b64encode(b"odoo").decode()}
            )

    class DummyHandler:
        def __init__(self):
            self.odoo_user_secret = DummySecret()
            self.spec = {}
            self.namespace = "default"
            self.owner_reference = {"fake": "owner"}
            self.name = "test"

    handler = DummyHandler()
    spec = {}
    if admin_password is not None:
        spec["adminPassword"] = admin_password
    if extra_config:
        spec["configOptions"] = extra_config
    handler.spec = spec
    return handler


def test_configmap_without_admin_password():
    """Config map should omit admin_passwd when not provided."""
    handler = _make_handler()
    conf = OdooConf(handler)
    cm = conf._get_resource_body()
    text = cm.data["odoo.conf"]
    assert "admin_passwd" not in text
    assert "db_user = odoo" in text


def test_configmap_with_admin_password_is_hashed():
    """Provided adminPassword should be hashed (pbkdf2) and present."""
    handler = _make_handler(admin_password="secret123")
    conf = OdooConf(handler)
    cm = conf._get_resource_body()
    text = cm.data["odoo.conf"]
    assert "admin_passwd" in text
    # Ensure it is not stored in plaintext
    assert "secret123" not in text
    # Basic pbkdf2 marker (passlib uses pbkdf2-sha512 for the scheme name)
    assert "$pbkdf2-sha512$" in text


def test_configmap_respects_extra_config():
    """configOptions should be merged into the generated config."""
    handler = _make_handler(extra_config={"log_level": "debug", "custom": "yes"})
    conf = OdooConf(handler)
    cm = conf._get_resource_body()
    text = cm.data["odoo.conf"]
    assert "log_level = debug" in text
    assert "custom = yes" in text
