from kubernetes import client
from .resource_handler import ResourceHandler, update_if_exists, create_if_missing
import os


class Deployment(ResourceHandler):
    """Manages the Odoo Deployment."""

    def __init__(self, handler):
        super().__init__(handler)
        self.defaults = handler.defaults
        self.odoo_user_secret = handler.odoo_user_secret

    def _read_resource(self):
        return client.AppsV1Api().read_namespaced_deployment(
            name=self.name,
            namespace=self.namespace,
        )

    @update_if_exists
    def handle_create(self):
        deployment = self._get_resource_body()
        self._resource = client.AppsV1Api().create_namespaced_deployment(
            namespace=self.namespace,
            body=deployment,
        )

    @create_if_missing
    def handle_update(self):
        deployment = self._get_resource_body()
        self._resource = client.AppsV1Api().patch_namespaced_deployment(
            name=self.name,
            namespace=self.namespace,
            body=deployment,
        )

    def handle_delete(self):
        # Scale down the deployment before deletion
        if self.resource:
            self.resource.spec.replicas = 0
            client.AppsV1Api().patch_namespaced_deployment(
                name=self.name,
                namespace=self.namespace,
                body=self.resource,
            )

    def _get_resource_body(self):
        db_host = os.environ["DB_HOST"]
        db_port = os.environ["DB_PORT"]

        image = self.spec.get("image", self.defaults.get("odooImage", "odoo:18.0"))

        metadata = client.V1ObjectMeta(
            name=self.name,
            owner_references=[self.owner_reference],
            labels={"app": self.name},
        )

        pull_secret = (
            {
                "image_pull_secrets": [
                    client.V1LocalObjectReference(
                        name=f"{self.spec.get('imagePullSecret')}"
                    )
                ]
            }
            if self.spec.get("imagePullSecret")
            else {}
        )

        spec = client.V1DeploymentSpec(
            replicas=1,
            selector=client.V1LabelSelector(match_labels={"app": self.name}),
            strategy={"type": "Recreate"},
            template=client.V1PodTemplateSpec(
                metadata=client.V1ObjectMeta(
                    labels={"app": self.name},
                ),
                spec=client.V1PodSpec(
                    **pull_secret,
                    volumes=[
                        client.V1Volume(
                            name=f"filestore",
                            persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(
                                claim_name=f"{self.name}-filestore-pvc"
                            ),
                        ),
                        client.V1Volume(
                            name="odoo-conf",
                            config_map=client.V1ConfigMapVolumeSource(
                                name=f"{self.name}-odoo-conf"
                            ),
                        ),
                    ],
                    security_context=client.V1PodSecurityContext(
                        run_as_user=1001,
                        run_as_group=1001,
                        fs_group=1001,
                    ),
                    affinity=self.spec.get(
                        "affinity", self.defaults.get("affinity", {})
                    ),
                    tolerations=self.spec.get(
                        "tolerations", self.defaults.get("tolerations", [])
                    ),
                    containers=[
                        client.V1Container(
                            name=f"odoo-{self.name}",
                            image=image,
                            ports=[
                                client.V1ContainerPort(
                                    container_port=8069,
                                    name="http",
                                ),
                                client.V1ContainerPort(
                                    container_port=8072,
                                    name="websocket",
                                ),
                            ],
                            volume_mounts=[
                                client.V1VolumeMount(
                                    name="filestore",
                                    mount_path="/var/lib/odoo",
                                ),
                                client.V1VolumeMount(
                                    name="odoo-conf",
                                    mount_path="/etc/odoo",
                                ),
                            ],
                            env=[
                                client.V1EnvVar(
                                    name="HOST",
                                    value=db_host,
                                ),
                                client.V1EnvVar(
                                    name="PORT",
                                    value=db_port,
                                ),
                                client.V1EnvVar(
                                    name="USER",
                                    value_from=client.V1EnvVarSource(
                                        secret_key_ref=client.V1SecretKeySelector(
                                            name=f"{self.name}-odoo-user",
                                            key="username",
                                        )
                                    ),
                                ),
                                client.V1EnvVar(
                                    name="PASSWORD",
                                    value_from=client.V1EnvVarSource(
                                        secret_key_ref=client.V1SecretKeySelector(
                                            name=f"{self.name}-odoo-user",
                                            key="password",
                                        )
                                    ),
                                ),
                            ],
                            resources=self.spec.get(
                                "resources",
                                self.defaults.get("resources", {}),
                            ),
                            liveness_probe=client.V1Probe(
                                http_get=client.V1HTTPGetAction(
                                    path="/web/health",
                                    port=8069,
                                ),
                                initial_delay_seconds=20,
                                period_seconds=5,
                                timeout_seconds=2,
                                success_threshold=1,
                                failure_threshold=36,
                            ),
                            readiness_probe=client.V1Probe(
                                http_get=client.V1HTTPGetAction(
                                    path="/web/health",
                                    port=8069,
                                ),
                                initial_delay_seconds=20,
                                period_seconds=5,
                                timeout_seconds=2,
                                success_threshold=1,
                                failure_threshold=20,
                            ),
                        )
                    ],
                ),
            ),
        )

        return client.V1Deployment(
            metadata=metadata,
            spec=spec,
        )
