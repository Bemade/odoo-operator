from types import SimpleNamespace

from kubernetes import client
from kubernetes.client.rest import ApiException

from handlers.job_handler import JobHandler


class DummyJobHandler(JobHandler):
    def __init__(self, handler, **kwargs):
        super().__init__(
            handler, status_key="jobName", status_phase="Working", **kwargs
        )

    def _get_resource_body(self):
        return client.V1Job(metadata=client.V1ObjectMeta(name="job-1"))


def test_handle_create_sets_status(monkeypatch):
    handler = SimpleNamespace(
        name="demo",
        namespace="default",
        spec={},
        resource={"status": {}},
        owner_reference={"fake": "owner"},
    )
    status_calls = []
    monkeypatch.setattr(
        "handlers.job_handler.client.BatchV1Api.create_namespaced_job",
        lambda self, namespace, body: client.V1Job(
            metadata=client.V1ObjectMeta(name="job-1")
        ),
    )
    monkeypatch.setattr(
        "handlers.job_handler.client.CustomObjectsApi.patch_namespaced_custom_object_status",
        lambda *args, **kwargs: status_calls.append(kwargs["body"]),
    )
    dj = DummyJobHandler(handler)
    dj.handle_create()
    assert status_calls and status_calls[-1]["status"]["phase"] == "Working"
    assert status_calls[-1]["status"]["jobName"] == "job-1"


def test_handle_update_completes(monkeypatch):
    handler = SimpleNamespace(
        name="demo",
        namespace="default",
        spec={},
        resource={"status": {"jobName": "job-1"}},
        owner_reference={"fake": "owner"},
        deployment=SimpleNamespace(name="demo-deploy"),
    )
    status_calls = []
    patch_calls = []

    job_obj = SimpleNamespace(status=SimpleNamespace(succeeded=1, failed=None))
    monkeypatch.setattr(
        "handlers.job_handler.client.BatchV1Api.read_namespaced_job",
        lambda self, name, namespace: job_obj,
    )
    monkeypatch.setattr(
        "handlers.job_handler.client.CustomObjectsApi.patch_namespaced_custom_object_status",
        lambda *args, **kwargs: status_calls.append(kwargs["body"]),
    )
    monkeypatch.setattr(
        "handlers.job_handler.client.CustomObjectsApi.patch_namespaced_custom_object",
        lambda *args, **kwargs: patch_calls.append(kwargs["body"]),
    )
    dj = DummyJobHandler(
        handler, completion_patch={"metadata": {"labels": {"done": "1"}}}
    )
    dj.handle_update()
    assert status_calls and status_calls[-1]["status"]["phase"] == "Running"
    assert status_calls[-1]["status"]["jobName"] is None
    assert patch_calls and patch_calls[-1]["metadata"]["labels"]["done"] == "1"


def test_is_running_retry_on_404(monkeypatch):
    handler = SimpleNamespace(
        name="demo",
        namespace="default",
        spec={},
        resource={"status": {"jobName": "job-1"}},
        owner_reference={"fake": "owner"},
    )

    calls = {"tries": 0}

    def fake_read(self, name, namespace):
        calls["tries"] += 1
        if calls["tries"] < 2:
            raise ApiException(status=404)
        return SimpleNamespace(status=SimpleNamespace(succeeded=None, failed=None))

    monkeypatch.setattr(
        "handlers.job_handler.client.BatchV1Api.read_namespaced_job",
        fake_read,
    )

    dj = DummyJobHandler(handler)
    assert dj.is_running is True


def test_is_running_returns_false_when_missing(monkeypatch):
    handler = SimpleNamespace(
        name="demo",
        namespace="default",
        spec={},
        resource={"status": {"jobName": "job-1"}},
        owner_reference={"fake": "owner"},
    )

    monkeypatch.setattr(
        "handlers.job_handler.client.BatchV1Api.read_namespaced_job",
        lambda self, name, namespace: (_ for _ in ()).throw(ApiException(status=404)),
    )
    dj = DummyJobHandler(handler)
    assert dj.is_running is False
