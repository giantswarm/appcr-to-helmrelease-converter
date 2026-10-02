import pytest

from converter import convert
from converter.release_chart import (
    CLUSTER_CHART_PROVIDERS,
    cluster_chart_name,
    cluster_chart_provider,
    pinned_cluster_chart_version,
    release_chart_name,
    release_cr_name,
    release_version_from_cluster,
    substitute_release_chart,
)


def _app(chart="cluster-aws", version="7.2.5"):
    return {
        "metadata": {"name": "mycluster", "namespace": "org-acme"},
        "spec": {"name": chart, "namespace": "org-acme", "version": version, "catalog": "cluster"},
    }


_CATALOG = {"spec": {"repositories": [{"type": "oci", "URL": "oci://gsoci.azurecr.io/charts/giantswarm/"}]}}


class TestClusterChartProvider:
    @pytest.mark.parametrize("provider", CLUSTER_CHART_PROVIDERS)
    def test_known_providers_qualify(self, provider):
        assert cluster_chart_provider(_app(chart=f"cluster-{provider}")) == provider

    def test_lookalike_chart_does_not_qualify(self):
        assert cluster_chart_provider(_app(chart="cluster-autoscaler")) is None

    def test_bare_cluster_chart_does_not_qualify(self):
        assert cluster_chart_provider(_app(chart="cluster")) is None

    def test_ordinary_chart_does_not_qualify(self):
        assert cluster_chart_provider(_app(chart="loki")) is None

    def test_release_chart_does_not_qualify(self):
        assert cluster_chart_provider(_app(chart="release-aws")) is None

    def test_missing_spec_does_not_qualify(self):
        assert cluster_chart_provider({}) is None

    def test_metadata_name_is_ignored(self):
        app = _app(chart="loki")
        app["metadata"]["name"] = "cluster-aws"
        assert cluster_chart_provider(app) is None


class TestNames:
    def test_cluster_chart_name(self):
        assert cluster_chart_name("cloud-director") == "cluster-cloud-director"

    def test_release_chart_name(self):
        assert release_chart_name("cloud-director") == "release-cloud-director"

    def test_release_cr_name(self):
        assert release_cr_name("aws", "34.0.0") == "aws-34.0.0"


class TestReleaseVersionFromCluster:
    def test_reads_label(self):
        cluster = {"metadata": {"labels": {"release.giantswarm.io/version": "34.0.0"}}}
        assert release_version_from_cluster(cluster) == "34.0.0"

    def test_strips_leading_v(self):
        cluster = {"metadata": {"labels": {"release.giantswarm.io/version": "v34.0.0"}}}
        assert release_version_from_cluster(cluster) == "34.0.0"

    def test_missing_label_is_none(self):
        assert release_version_from_cluster({"metadata": {"labels": {}}}) is None

    def test_missing_metadata_is_none(self):
        assert release_version_from_cluster({}) is None


class TestPinnedClusterChartVersion:
    def test_reads_matching_component(self):
        release = {"spec": {"components": [
            {"name": "flatcar", "version": "4459.2.2"},
            {"name": "cluster-aws", "catalog": "cluster", "version": "7.2.5"},
        ]}}
        assert pinned_cluster_chart_version(release, "aws") == "7.2.5"

    def test_ignores_apps(self):
        release = {"spec": {"apps": [{"name": "cluster-aws", "version": "1.0.0"}], "components": []}}
        assert pinned_cluster_chart_version(release, "aws") is None

    def test_missing_spec_is_none(self):
        assert pinned_cluster_chart_version({}, "aws") is None


class TestSubstituteReleaseChart:
    def test_replaces_chart_name_and_version(self):
        result = substitute_release_chart(_app(), "aws", "34.0.0")
        assert result["spec"]["name"] == "release-aws"
        assert result["spec"]["version"] == "34.0.0"

    def test_leaves_everything_else_untouched(self):
        app = _app()
        result = substitute_release_chart(app, "aws", "34.0.0")
        assert result["metadata"] == app["metadata"]
        assert {k: v for k, v in result["spec"].items() if k not in ("name", "version")} == \
            {k: v for k, v in app["spec"].items() if k not in ("name", "version")}

    def test_does_not_mutate_input(self):
        app = _app()
        substitute_release_chart(app, "aws", "34.0.0")
        assert app["spec"]["name"] == "cluster-aws"
        assert app["spec"]["version"] == "7.2.5"

    def test_converted_resources_differ_only_in_chart_and_tag(self):
        app = _app()
        plain = convert(app, _CATALOG)
        substituted = convert(substitute_release_chart(app, "aws", "34.0.0"), _CATALOG)
        assert substituted[0]["spec"]["url"] == "oci://gsoci.azurecr.io/charts/giantswarm/release-aws"
        assert substituted[0]["spec"]["ref"]["tag"] == "34.0.0"
        plain[0]["spec"]["url"] = substituted[0]["spec"]["url"]
        plain[0]["spec"]["ref"]["tag"] = "34.0.0"
        assert plain == substituted
