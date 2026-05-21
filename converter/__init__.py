from collections import OrderedDict
from typing import Any, List

from converter.resources import build_helm_release, build_oci_repository


def convert(app_dict: dict, catalog_dict: dict) -> List[OrderedDict[Any, Any]]:
    return [build_oci_repository(app_dict, catalog_dict), build_helm_release(app_dict)]
