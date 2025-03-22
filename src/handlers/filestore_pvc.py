from kubernetes import client
from .resource_handler import ResourceHandler, update_if_exists, create_if_missing


class FilestorePVC(ResourceHandler):
    """Manages the Odoo filestore Persistent Volume Claim."""

    def __init__(self, handler):
        super().__init__(handler)
        self.defaults = handler.defaults

    def _read_resource(self):
        return client.CoreV1Api().read_namespaced_persistent_volume_claim(
            name=f"{self.name}-filestore-pvc",
            namespace=self.namespace,
        )

    @update_if_exists
    def handle_create(self):
        pvc = self._get_resource_body()
        self._resource = client.CoreV1Api().create_namespaced_persistent_volume_claim(
            namespace=self.namespace,
            body=pvc,
        )

    @create_if_missing
    def handle_update(self):
        current_pvc = self._get_resource_body()
        requested_size = current_pvc.spec.resources.requests["storage"]
        current_resource_size = self.resource.spec.resources.requests["storage"]
        if requested_size > current_resource_size:
            self.resource.spec.resources.requests["storage"] = requested_size
            self._resource = (
                client.CoreV1Api().patch_namespaced_persistent_volume_claim(
                    name=self._resource.metadata.name,
                    namespace=self._resource.metadata.namespace,
                    body=self._resource,
                )
            )
        return



    @property
    def resource(self):
        if not self._resource:
            try:
                self._resource = self._read_resource()
            except client.exceptions.ApiException as e:
                if e.status == 404:
                    # Resource not found, that's fine
                    pass
                else:
                    raise
        return self._resource

    def _get_resource_body(self):
        spec = self.spec.get("filestore", {})
        size = spec.get("size", self.defaults.get("filestoreSize", "2Gi"))
        storage_class = spec.get(
            "storageClass", self.defaults.get("storageClass", "standard")
        )
        metadata = client.V1ObjectMeta(
            name=f"{self.name}-filestore-pvc",
            owner_references=[self.owner_reference],
        )
        return client.V1PersistentVolumeClaim(
            metadata=metadata,
            spec=client.V1PersistentVolumeClaimSpec(
                access_modes=["ReadWriteOnce"],
                storage_class_name=storage_class,
                resources=client.V1ResourceRequirements(requests={"storage": size}),
            ),
        )
