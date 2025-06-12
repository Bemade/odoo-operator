from .resource_handler import ResourceHandler
from kubernetes import client
import kopf


class GitSyncJobHandler(ResourceHandler):
    def __init__(self, body, **kwargs):
        self.body = body
        self.spec = body.get("spec", {})
        self.meta = body.get("meta", body.get("metadata"))
        self.namespace = self.meta.get("namespace")
        self.name = self.meta.get("name")
        self.uid = self.meta.get("uid")
        self.owner_references = self.meta.get("ownerReferences", [])
        self._resource = None

    def _read_resource(self):
        return client.BatchV1Api().read_namespaced_job(
            name=self.name,
            namespace=self.namespace,
        )

    def handle_update(self):
        job: client.V1Job = self.resource
        status = job.status
        succeeded = status.get("succeeded", False)
        failed = status.get("failed", False)
        if succeeded or failed:
            owner_refs = job.metadata.owner_references
            for ref in owner_refs:
                if ref.kind == "GitSync":
                    git_sync_name = ref.name
                    git_sync = client.CustomObjectsApi().get_namespaced_custom_object(
                        group="bemade.org",
                        version="v1",
                        namespace=self.namespace,
                        plural="gitsyncs",
                        name=git_sync_name,
                    )
                    client.CustomObjectsApi().patch_namespaced_custom_object(
                        group="bemade.org",
                        version="v1",
                        namespace=self.namespace,
                        plural="gitsyncs",
                        name=git_sync_name,
                        body={
                            "status": {
                                "succeeded": succeeded,
                                "failed": failed,
                            }
                        },
                    )
                    return
            raise kopf.AdmissionError("GitSync not found")
