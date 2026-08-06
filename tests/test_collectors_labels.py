"""The one tolerant label accessor, shared by the Docker and Traefik collectors."""

from terminal_status_panel.collectors._labels import container_labels


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
