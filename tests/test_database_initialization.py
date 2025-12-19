import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# Ensure src is importable when running pytest from repo root
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from handlers.database_initialization import DatabaseInitializationHandler  # noqa: E402


def _make_handler(mode="fresh", restore_cfg=None):
    spec = {"initialization": {"mode": mode}}
    if restore_cfg is not None:
        spec["initialization"]["restore"] = restore_cfg
    return SimpleNamespace(
        name="demo",
        namespace="default",
        uid="1234-5678",
        spec=spec,
    )


def test_init_fresh_no_action(monkeypatch):
    handler = _make_handler(mode="fresh")
    calls = []
    monkeypatch.setattr(
        "handlers.database_initialization.client.CustomObjectsApi.patch_namespaced_custom_object",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    DatabaseInitializationHandler(handler).handle_create()
    assert calls == []


def test_init_restore_with_config(monkeypatch):
    restore_cfg = {
        "url": "https://source.odoo",
        "sourceDatabase": "prod",
        "masterPassword": "mpp",
        "withFilestore": False,
        "neutralize": False,
    }
    handler = _make_handler(mode="restore", restore_cfg=restore_cfg)
    calls = []

    def fake_patch(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(
        "handlers.database_initialization.client.CustomObjectsApi.patch_namespaced_custom_object",
        fake_patch,
    )

    DatabaseInitializationHandler(handler).handle_create()
    assert len(calls) == 1
    _, kwargs = calls[0]
    body = kwargs["body"]
    restore = body["spec"]["restore"]
    assert restore["enabled"] is True
    assert restore["url"] == "https://source.odoo"
    assert restore["sourceDatabase"] == "prod"
    assert restore["targetDatabase"] == "odoo_1234_5678"
    assert restore["masterPassword"] == "mpp"
    assert restore["withFilestore"] is False
    assert restore["neutralize"] is False


def test_init_restore_missing_config(monkeypatch, caplog):
    handler = _make_handler(mode="restore", restore_cfg=None)
    calls = []
    monkeypatch.setattr(
        "handlers.database_initialization.client.CustomObjectsApi.patch_namespaced_custom_object",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    with caplog.at_level("WARNING"):
        DatabaseInitializationHandler(handler)._handle_restore_initialization()
    assert calls == []
    assert any("no restore config" in msg for msg in caplog.messages)
