"""The one tolerant label accessor, shared by the Docker and Traefik collectors."""

from terminal_status_panel.collectors._labels import (
    COMPOSE_PROJECT_LABEL,
    COMPOSE_SERVICE_LABEL,
    compose_identity,
    container_labels,
)


class _Fake:
    def __init__(self, attrs):
        self.attrs = attrs


def test_the_inspect_shape_is_read():
    """`containers.list()` issues a full inspect: labels sit under Config."""
    c = _Fake({"Config": {"Labels": {"a": "1"}}})
    assert container_labels(c) == {"a": "1"}


def test_the_sparse_shape_is_read():
    """`containers.list(sparse=True)` returns the raw list API: labels at top level."""
    c = _Fake({"Labels": {"a": "1"}})
    assert container_labels(c) == {"a": "1"}


def test_config_wins_when_both_are_present():
    c = _Fake({"Config": {"Labels": {"a": "config"}}, "Labels": {"a": "top"}})
    assert container_labels(c) == {"a": "config"}


def test_neither_shape_yields_an_empty_mapping():
    assert container_labels(_Fake({})) == {}
    assert container_labels(_Fake(None)) == {}


def test_a_non_mapping_label_value_does_not_raise():
    """A malformed daemon response must not escape as an AttributeError."""
    assert container_labels(_Fake({"Config": {"Labels": ["not", "a", "mapping"]}})) == {}


# --- the one Compose-identity rule -------------------------------------------


def test_a_compose_container_is_named_by_its_service_label():
    labels = {COMPOSE_PROJECT_LABEL: "portal", COMPOSE_SERVICE_LABEL: "db"}
    assert compose_identity(labels, "portal-db-1") == "db"


def test_a_container_with_no_compose_labels_keeps_its_own_name():
    assert compose_identity({}, "standalone-proxy") == "standalone-proxy"


def test_a_service_label_without_a_project_label_is_not_an_identity():
    """A service name identifies something only within its project, and
    Compose never writes one label without the other. Honouring the service
    label alone made the Traefik collector call this container `db` while the
    Docker collector called it `legacy-box`."""
    assert compose_identity({COMPOSE_SERVICE_LABEL: "db"}, "legacy-box") == "legacy-box"


def test_an_empty_service_label_falls_back_to_the_container_name():
    labels = {COMPOSE_PROJECT_LABEL: "portal", COMPOSE_SERVICE_LABEL: ""}
    assert compose_identity(labels, "portal-db-1") == "portal-db-1"
