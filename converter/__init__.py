from converter.resources import build_helm_release, build_oci_repository
from converter.values_from import calculate_values_from

import yaml
from collections import OrderedDict
from typing import Any, List


def convert(content: str) -> List[OrderedDict[Any, Any]]:
    app = yaml.safe_load(content)
    return [build_oci_repository(app), build_helm_release(app)]
