from kubernetes import client
from .resource_handler import ResourceHandler, update_if_exists, create_if_missing


class Service(ResourceHandler):
    """Manages the Odoo Service."""

    def _read_resource(self):
        return client.CoreV1Api().read_namespaced_service(
            name=self.name,
            namespace=self.namespace,
        )

    @update_if_exists
    def handle_create(self):
        service = self._get_resource_body()
        self._resource = client.CoreV1Api().create_namespaced_service(
            namespace=self.namespace,
            body=service,
        )

    @create_if_missing
    def handle_update(self):
        # There is never anything to update here
        pass

    def _get_resource_body(self):
        metadata = client.V1ObjectMeta(
            name=self.name,
            owner_references=[self.owner_reference],
            labels={"app": self.name},
        )
        return client.V1Service(
            metadata=metadata,
            spec=client.V1ServiceSpec(
                selector={"app": self.name},
                ports=[
                    client.V1ServicePort(
                        port=8069,
                        target_port=8069,
                        name="http",
                    ),
                    client.V1ServicePort(
                        port=8072,
                        target_port=8072,
                        name="websocket",
                    ),
                ],
                type="ClusterIP",
            ),
        )
