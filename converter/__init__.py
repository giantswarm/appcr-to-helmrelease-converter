from collections import OrderedDict
from typing import Any, List

from converter.resources import (
    build_helm_release_and_oci_repo,
    build_helm_release_and_helm_repo,
    catalog_has_oci,
    override_catalog_registry,
)


def convert(
    app_dict: dict, catalog_dict: dict, resolution=None, pull_secret: str | None = None
) -> List[OrderedDict[Any, Any]]:
    if catalog_has_oci(catalog_dict):
        return build_helm_release_and_oci_repo(app_dict, catalog_dict, resolution, pull_secret)
    return build_helm_release_and_helm_repo(app_dict, catalog_dict, resolution, pull_secret)
