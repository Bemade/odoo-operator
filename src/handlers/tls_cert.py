from kubernetes import client
from .resource_handler import ResourceHandler, update_if_exists, create_if_missing


class TLSCert(ResourceHandler):
    """Manages the TLS Certificate for Odoo instance."""

    def __init__(self, handler):
        super().__init__(handler)
        self.host = self.spec.get("ingress").get("hosts")[0]

    def _read_resource(self):
        return client.CustomObjectsApi().get_namespaced_custom_object(
            group="cert-manager.io",
            version="v1",
            namespace=self.namespace,
            plural="certificates",
            name=f"{self.host}-cert",
        )

    @update_if_exists
    def handle_create(self):
        cert_definition = self._get_resource_body()
        self._resource = client.CustomObjectsApi().create_namespaced_custom_object(
            group="cert-manager.io",
            version="v1",
            namespace=self.namespace,
            plural="certificates",
            body=cert_definition,
        )

    @create_if_missing
    def handle_update(self):
        cert_definition = self._get_resource_body()

        self._resource = client.CustomObjectsApi().patch_namespaced_custom_object(
            group="cert-manager.io",
            version="v1",
            namespace=self.namespace,
            plural="certificates",
            name=f"{self.host}-cert",
            body=cert_definition,
        )

    def _get_resource_body(self):
        hostnames = self.spec.get("ingress").get("hosts")
        apiVersion = "cert-manager.io/v1"
        metadata = client.V1ObjectMeta(
            name=f"{self.host}-cert",
            owner_references=[self.owner_reference],
        )
        cert_spec = {
            "secretName": f"{self.host}-cert",
            "dnsNames": hostnames,
            "issuerRef": {
                "name": self.spec.get("ingress").get("issuer"),
                "kind": "ClusterIssuer",
            },
        }
        return {
            "apiVersion": apiVersion,
            "kind": "Certificate",
            "metadata": metadata,
            "spec": cert_spec,
        }
