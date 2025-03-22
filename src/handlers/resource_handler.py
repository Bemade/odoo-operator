from kubernetes import client
import functools


def update_if_exists(func):
    """Decorator that calls handle_update if the resource already exists."""

    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        if not self.resource:
            return func(self, *args, **kwargs)
        else:
            return self.handle_update()

    return wrapper


def create_if_missing(func):
    """Decorator that calls handle_create if the resource doesn't exist."""

    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        if not self.resource:
            return self.handle_create()
        else:
            return func(self, *args, **kwargs)

    return wrapper


class ResourceHandler:
    """
    Abstract class meant for handling creation, update and deletion of a Kubernetes resource.

    Subclasses must implement the following methods:
    - handle_create
    - handle_update
    - handle_delete

    For handle_create methods, use the @update_if_exists decorator to automatically
    call handle_update if the resource already exists.

    For handle_update methods, use the @create_if_missing decorator to automatically
    call handle_create if the resource doesn't exist.

    The default implementations of handle_update and handle_delete do nothing,
    so you only need to override them if you need custom behavior.
    """

    def __init__(self, handler=None):
        if handler:
            self.handler = handler
            self.spec = handler.spec
            self.namespace = handler.namespace
            self.owner_reference = handler.owner_reference
            self.name = handler.name
        self._resource = None

    @update_if_exists
    def handle_create(self):
        self._create_resource()

    @create_if_missing
    def handle_update(self):
        # Default implementation does nothing
        pass

    def handle_delete(self):
        # Default implementation does nothing
        pass

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

    def _read_resource(self):
        raise NotImplementedError()
