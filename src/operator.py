import kopf
from handlers import OdooHandler


@kopf.on.create("bemade.org", "v1", "odooinstances")
def create_fn(body, **kwargs):
    handler = OdooHandler(body, **kwargs)
    handler.on_create()


@kopf.on.update("bemade.org", "v1", "odooinstances")
def update_fn(body, **kwargs):
    handler = OdooHandler(body, **kwargs)
    handler.on_update()


@kopf.on.delete("bemade.org", "v1", "odooinstances")
def delete_fn(body, **kwargs):
    handler = OdooHandler(body, **kwargs)
    handler.on_delete()
