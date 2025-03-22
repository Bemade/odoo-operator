from kubernetes import client
from .resource_handler import ResourceHandler, update_if_exists, create_if_missing


class PullSecret(ResourceHandler):
    """Manages the image pull secret for Odoo."""

    def __init__(self, handler):
        super().__init__(handler)
        self.operator_ns = handler.operator_ns

    def _read_resource(self):
        return client.CoreV1Api().read_namespaced_secret(
            name=f"{self.spec.get('imagePullSecret')}",
            namespace=self.namespace,
        )

    @update_if_exists
    def handle_create(self):
        if self.spec.get("imagePullSecret"):
            pull_secret = self._get_resource_body()
            self._resource = client.CoreV1Api().create_namespaced_secret(
                namespace=self.namespace,
                body=pull_secret,
            )

    def _get_resource_body(self):
        orig_secret = client.CoreV1Api().read_namespaced_secret(
            name=f"{self.spec.get('imagePullSecret')}",
            namespace=self.operator_ns,
        )
        return client.V1Secret(
            metadata=client.V1ObjectMeta(
                name=f"{self.spec.get('imagePullSecret')}",
                owner_references=[self.owner_reference],
            ),
            type="kubernetes.io/dockerconfigjson",
            data=orig_secret.data,
        )
