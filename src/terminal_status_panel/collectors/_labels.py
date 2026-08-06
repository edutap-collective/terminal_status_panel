"""Reading labels off a Docker container, whichever response shape produced it.

Shared by the Docker and Traefik collectors. One copy on purpose: two copies of
this guard drifting apart is exactly how the sparse-shape trap gets re-opened.
"""

from __future__ import annotations

#: A container carrying this label is a Swarm task, whose labels are its
#: service's own. Any collector that reads both services and containers must
#: skip these, or every Swarm object is counted twice on a manager node.
SWARM_SERVICE_LABEL = "com.docker.swarm.service.name"


def _mapping(value) -> dict:
    return value if isinstance(value, dict) else {}


def container_labels(container) -> dict:
    """Every label on *container*, whichever response shape produced it.

    ``containers.list()`` issues a full inspect per container -- docker-py
    implements ``list()`` as one ``get()`` per result -- and carries labels
    under ``attrs["Config"]["Labels"]``. ``containers.list(sparse=True)``, which
    ``ContainerIndex`` in ``clusters.py`` already uses, returns the raw list-API
    response instead: labels sit at the top level, ``attrs["Labels"]``, and
    there is no ``Config`` key at all.

    Preferring ``Config.Labels`` and falling back to the top-level key keeps
    every caller working under either shape. Switching a caller to sparse
    without this guard would silently strip every container of its labels --
    with no error and no failing test.
    """
    attrs = _mapping(getattr(container, "attrs", None))
    config = _mapping(attrs.get("Config"))
    labels = config.get("Labels")
    if isinstance(labels, dict):
        return dict(labels)
    return dict(_mapping(attrs.get("Labels")))
