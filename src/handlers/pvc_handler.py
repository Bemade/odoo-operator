import logging
from kubernetes import client
from .resource_handler import ResourceHandler, update_if_exists, create_if_missing


class PVCHandler(ResourceHandler):
    """Base class for PVC resource handlers with common functionality."""

    def __init__(
        self,
        handler,
        pvc_name_suffix,
        default_size,
    ):
        """Initialize the PVC handler.

        Args:
            handler: The parent resource handler
            pvc_name_suffix: Suffix to append to resource name for the PVC
            default_size: Default size if not specified in the spec
        """
        super().__init__(handler)
        self.pvc_name_suffix = pvc_name_suffix
        self.default_size = default_size

    def _get_pvc_name(self):
        """Get the name of the PVC."""
        return f"{self.name}-{self.pvc_name_suffix}"

    def _read_resource(self):
        """Read the PVC from the cluster."""
        try:
            return client.CoreV1Api().read_namespaced_persistent_volume_claim(
                name=self._get_pvc_name(),
                namespace=self.namespace,
            )
        except client.exceptions.ApiException as e:
            if e.status == 404:
                return None
            raise

    def _get_storage_size(self, spec_path=None):
        """Get the storage size from the spec with fallback to default."""
        if spec_path:
            # If a spec path is provided (e.g., ['gitProject', 'storage', 'size'])
            current = self.spec
            for key in spec_path[:-1]:
                current = current.get(key, {})
            return current.get(spec_path[-1], self.default_size)
        return self.default_size

    def _get_resource_body(self):
        """Get the PVC definition. Must be implemented by subclasses."""
        raise NotImplementedError("Subclasses must implement _get_resource_body")

    @update_if_exists
    def handle_create(self):
        """Create the PVC if it doesn't exist."""
        pvc = self._get_resource_body()
        self._resource = client.CoreV1Api().create_namespaced_persistent_volume_claim(
            namespace=self.namespace,
            body=pvc,
        )

    @create_if_missing
    def handle_update(self):
        """Handle updates to the PVC, only allowing size increases.

        This is a simplified implementation that relies on Kubernetes to handle
        storage quantity parsing and comparison.
        """
        current_pvc = self._get_resource_body()
        requested_size = current_pvc.spec.resources.requests["storage"]

        # Get the current resource if it exists
        try:
            self._resource = self._read_resource()
        except client.exceptions.ApiException as e:
            if e.status == 404:
                logging.warning(f"PVC {self._get_pvc_name()} not found, creating it")
                return self.handle_create()
            raise

        # Only update if the requested size is larger than current
        current_size = self._resource.spec.resources.requests["storage"]
        if requested_size > current_size:
            self._resource.spec.resources.requests["storage"] = requested_size
            self._resource = (
                client.CoreV1Api().patch_namespaced_persistent_volume_claim(
                    name=self._get_pvc_name(),
                    namespace=self.namespace,
                    body=self._resource,
                )
            )
