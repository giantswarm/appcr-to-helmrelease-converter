from kubernetes import client, config
from kubernetes.client.exceptions import ApiException


class FetchError(Exception):
    pass


def fetch(name: str, namespace: str, context: str | None = None) -> tuple[dict, dict]:
    config.load_kube_config(context=context)
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
        raise FetchError(f"failed to fetch App {namespace}/{name}: {e}") from e

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
                raise FetchError(f"failed to fetch Catalog {catalog_name}: {e}") from e
            if catalog_namespace:
                raise FetchError(f"failed to fetch Catalog {catalog_name}: not found in {ns}: {e}") from e

    if catalog is None:
        raise FetchError(f"failed to fetch Catalog {catalog_name}: not found in default or giantswarm")

    return app, catalog
