"""
Initialize a fresh Odoo database.

This job runs once when an OdooInstance is created without a restore spec.
It creates and initializes the database with base modules.
"""

from __future__ import annotations
from .job_handler import JobHandler
from kubernetes import client
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .odoo_handler import OdooHandler


class InitJob(JobHandler):
    """Manages the Odoo Database Initialization Job."""

    def __init__(self, handler: OdooHandler):
        super().__init__(
            handler=handler,
            status_key="initJob",
            status_phase="Initializing",
            completion_patch=None,  # No patch needed after completion
        )
        self.defaults = handler.defaults
        # Generate database name from instance UID
        self.database = f"odoo_{handler.uid.replace('-', '_')}"
        self.initialization_spec = handler.spec.get("initialization", {})
        self.mode = self.initialization_spec.get("mode", "fresh")

    def handle_update(self):
        """Check for init job completion, but never create a new init job on update."""
        # Only check if the job has completed, don't create a new one
        if self.resource and self.resource.status:
            status = self.resource.status
            if status.succeeded or status.failed:
                # Update the OdooInstance status to show the job is completed
                from kubernetes import client as k8s_client

                k8s_client.CustomObjectsApi().patch_namespaced_custom_object_status(
                    group="bemade.org",
                    version="v1",
                    namespace=self.handler.namespace,
                    plural="odooinstances",
                    name=self.handler.name,
                    body={
                        "status": {
                            "phase": "Running",
                            self.status_key: None,
                        },
                    },
                )

    def _should_run(self):
        return self.mode == "fresh"

    def _get_resource_body(self):
        """Create the job resource definition."""
        image = self.spec.get("image", self.defaults.get("odooImage", "odoo:18.0"))

        metadata = client.V1ObjectMeta(
            generate_name=f"{self.name}-init-",
            namespace=self.namespace,
            owner_references=[self.owner_reference],
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

        volumes, volume_mounts = self.handler.deployment.get_volumes_and_mounts()

        # Create the job spec
        job_spec = client.V1JobSpec(
            template=client.V1PodTemplateSpec(
                metadata=metadata,
                spec=client.V1PodSpec(
                    **pull_secret,
                    restart_policy="Never",
                    volumes=volumes,
                    security_context=client.V1PodSecurityContext(
                        run_as_user=100,
                        run_as_group=101,
                        fs_group=101,
                    ),
                    affinity=self.spec.get(
                        "affinity", self.defaults.get("affinity", {})
                    ),
                    tolerations=self.spec.get(
                        "tolerations", self.defaults.get("tolerations", [])
                    ),
                    containers=[
                        client.V1Container(
                            name=f"odoo-init-{self.name}",
                            image=image,
                            command=["odoo"],
                            args=[
                                f"--db_host=$(HOST)",
                                f"--db_user=$(USER)",
                                f"--db_port=$(PORT)",
                                f"--db_password=$(PASSWORD)",
                                "-i",
                                "base",  # Initialize with base module
                                "-d",
                                f"{self.database}",
                                "--no-http",
                                "--stop-after-init",
                            ],
                            volume_mounts=volume_mounts,
                            env=self.handler.deployment.get_environment_variables(),
                            resources=self.spec.get(
                                "resources",
                                self.defaults.get("resources", {}),
                            ),
                        )
                    ],
                ),
            ),
            backoff_limit=0,
            ttl_seconds_after_finished=3600,  # Delete job 1 hour after completion
        )

        return client.V1Job(
            api_version="batch/v1",
            kind="Job",
            metadata=metadata,
            spec=job_spec,
        )
