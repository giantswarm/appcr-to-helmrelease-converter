import json

from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
from kubernetes.config.config_exception import ConfigException


class FetchError(Exception):
    pass


def _api_message(e: ApiException) -> str:
    try:
        return json.loads(e.body)["message"]
    except (TypeError, ValueError, KeyError):
        return e.reason or str(e.status)


def fetch(name: str, namespace: str, context: str | None = None) -> tuple[dict, dict]:
    try:
        config.load_kube_config(context=context)
    except ConfigException as e:
        raise FetchError(str(e)) from e
    api = client.CustomObjectsApi()

    try:
        app = api.get_namespaced_custom_object(
            group="application.giantswarm.io",
            version="v1alpha1",
            namespace=namespace,
            plural="apps",
            name=name,
        )
    except ApiException as e:
        raise FetchError(f"failed to fetch App {namespace}/{name}: {_api_message(e)}") from e

    catalog_name = app["spec"]["catalog"]
    catalog_namespace = app["spec"].get("catalogNamespace")

    namespaces = [catalog_namespace] if catalog_namespace else ["default", "giantswarm"]

    catalog = None
    for ns in namespaces:
        try:
            catalog = api.get_namespaced_custom_object(
                group="application.giantswarm.io",
                version="v1alpha1",
                namespace=ns,
                plural="catalogs",
                name=catalog_name,
            )
            break
        except ApiException as e:
            if e.status != 404:
                raise FetchError(f"failed to fetch Catalog {catalog_name}: {_api_message(e)}") from e
            if catalog_namespace:
                raise FetchError(f"failed to fetch Catalog {catalog_name}: not found in {ns}") from e

    if catalog is None:
        raise FetchError(f"failed to fetch Catalog {catalog_name}: not found in default or giantswarm")

    return app, catalog
