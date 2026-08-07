"""Reading labels off a Docker container, whichever response shape produced it.

Shared by the Docker and Traefik collectors. One copy on purpose: two copies of
this guard drifting apart is exactly how the sparse-shape trap gets re-opened.
"""

from __future__ import annotations

#: A container carrying this label is a Swarm task, whose labels are its
#: service's own. Any collector that reads both services and containers must
#: skip these, or every Swarm object is counted twice on a manager node.
SWARM_SERVICE_LABEL = "com.docker.swarm.service.name"

#: `docker compose` sets this to the *service* name -- ``db``, not the
#: container's own ``course-statistics-db`` -- on every container it starts.
COMPOSE_SERVICE_LABEL = "com.docker.compose.service"

#: `docker compose` sets this to the project name on every container it starts,
#: always beside ``COMPOSE_SERVICE_LABEL``. Its presence is what makes a
#: container part of a Compose project, and only inside a project does the
#: service name identify anything.
COMPOSE_PROJECT_LABEL = "com.docker.compose.project"


def _mapping(value) -> dict:
    return value if isinstance(value, dict) else {}


def compose_identity(labels: dict, container_name: str) -> str:
    """The name ``collectors/docker.py`` gives this container's ``ServiceStatus``.

    ``_container_groups`` in ``docker.py`` keys a Compose container's
    ``ServiceStatus.name`` by ``COMPOSE_SERVICE_LABEL``, falling back to the
    container's own name for one Compose never touched. Any other caller that
    needs to match a Docker identity coming from elsewhere -- a Traefik router
    label, for instance -- against ``ServiceStatus.name`` must compute the very
    same string. A caller that reimplements the rule instead of calling this is
    one edit away from the two silently drifting apart, at which point the
    match just stops firing, with no error and no failing test -- exactly the
    shape of bug this function exists to rule out.

    **The whole rule lives here, the project label included.** A service name
    identifies something only within its project, so a container that carries
    ``COMPOSE_SERVICE_LABEL`` without ``COMPOSE_PROJECT_LABEL`` -- which
    Compose never produces, but a hand-written label does -- is not a project
    member and keeps its own name. Leaving that half of the condition at the
    call site is what let the two collectors disagree: ``docker.py`` called
    such a container ``legacy-box`` while ``traefik.py`` called it ``db``, and
    the router pointing at it rendered a red "no such service" for a container
    sitting right there in the same listing.
    """
    if COMPOSE_PROJECT_LABEL not in labels:
        return container_name
    return labels.get(COMPOSE_SERVICE_LABEL) or container_name


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
