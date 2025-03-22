from kubernetes import client
import os
import yaml

from .pull_secret import PullSecret
from .odoo_user_secret import OdooUserSecret
from .filestore_pvc import FilestorePVC
from .odoo_conf import OdooConf
from .tls_cert import TLSCert
from .deployment import Deployment
from .service import Service
from .ingress_routes import IngressRouteHTTP, IngressRouteHTTPS, IngressRouteWebsocket
from .resource_handler import ResourceHandler


class OdooHandler(ResourceHandler):
    def __init__(self, body=None, **kwargs):
        if body:
            self.body = body
            self.spec = body.get("spec", {})
            self.meta = body.get("meta", body.get("metadata"))
            self.namespace = self.meta.get("namespace")
            self.name = self.meta.get("name")
            self.uid = self.meta.get("uid")
        else:
            self.body = {}
            self.spec = {}
            self.meta = {}
            self.namespace = None
            self.name = None
            self.uid = None

        self.operator_ns = os.environ.get("OPERATOR_NAMESPACE")

        # Load defaults if available
        try:
            with open("/etc/odoo/instance-defaults.yaml") as f:
                self.defaults = yaml.safe_load(f)
        except (FileNotFoundError, PermissionError):
            self.defaults = {}

        self._resource = None  # This will be an OdooInstance

        # Initialize all handlers in the correct order for creation/update
        # Each handler will check the spec to determine if it should create resources
        self.pull_secret = PullSecret(self)
        self.odoo_user_secret = OdooUserSecret(self)
        self.filestore_pvc = FilestorePVC(self)
        self.odoo_conf = OdooConf(self)
        self.tls_cert = TLSCert(self)
        self.deployment = Deployment(self)
        self.service = Service(self)
        self.ingress_route_http = IngressRouteHTTP(self)
        self.ingress_route_https = IngressRouteHTTPS(self)
        self.ingress_route_websocket = IngressRouteWebsocket(self)

        # Create handlers list in the order resources should be created/updated
        self.handlers = [
            self.pull_secret,
            self.odoo_user_secret,
            self.filestore_pvc,
            self.odoo_conf,
            self.tls_cert,
            self.deployment,
            self.service,
            self.ingress_route_http,
            self.ingress_route_https,
            self.ingress_route_websocket,
        ]

    def on_create(self):
        # Create all resources in the correct order
        for handler in self.handlers:
            handler.handle_create()

    def on_update(self):
        # Update all resources in the correct order
        for handler in self.handlers:
            handler.handle_update()

    def on_delete(self):
        # Delete resources in reverse order
        # The deployment handler will handle scaling down before deletion
        for handler in reversed(self.handlers):
            handler.handle_delete()

    @property
    def owner_reference(self):
        return client.V1OwnerReference(
            api_version="bemade.org/v1",
            kind="OdooInstance",
            name=self.name,
            uid=self.uid,
            block_owner_deletion=True,
        )
