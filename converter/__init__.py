from collections import OrderedDict
from typing import Any, List

from converter.resources import build_helm_release_and_oci_repo, build_helm_release_and_helm_repo


def _catalog_has_oci(catalog: dict) -> bool:
    for repo in (catalog.get("spec") or {}).get("repositories") or []:
        if repo.get("type") == "oci":
            return True
    storage = (catalog.get("spec") or {}).get("storage") or {}
    return storage.get("type") == "oci"


def convert(app_dict: dict, catalog_dict: dict, resolution=None) -> List[OrderedDict[Any, Any]]:
    if _catalog_has_oci(catalog_dict):
        return build_helm_release_and_oci_repo(app_dict, catalog_dict, resolution)
    return build_helm_release_and_helm_repo(app_dict, catalog_dict, resolution)
