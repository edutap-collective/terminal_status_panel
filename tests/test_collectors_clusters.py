from terminal_status_panel.collectors import clusters

PG_STATE = """\
               Name |  Node |                Host:Port |       TLI: LSN |   Connection |      Reported State |      Assigned State
--------------------+-------+--------------------------+----------------+--------------+---------------------+--------------------
pg18-lmzvd06-ccn-02 |     1 | pg18-lmzvd06-ccn-02:5432 |   1: 0/75243B8 |   read-write |             primary |             primary
pg18-lmzvd06-ccn-03 |     2 | pg18-lmzvd06-ccn-03:5432 |   1: 0/75243B8 |    read-only |           secondary |           secondary
pg18-lmzvd06-ccn-04 |     3 | pg18-lmzvd06-ccn-04:5432 |   1: 0/75243B8 |    read-only |           secondary |           secondary
pg18-lmzvd06-ccc-01 |     4 | pg18-lmzvd06-ccc-01:5432 |   1: 0/75243B8 |    read-only |           secondary |           secondary
pg18-lmzvd06-ccn-01 |     5 | pg18-lmzvd06-ccn-01:5432 |   1: 0/75243B8 |    read-only |           secondary |           secondary
"""


class _FakeContainer:
    def __init__(self, name, exec_result=(0, b""), env=None):
        self.name = name
        self._exec_result = exec_result
        self.attrs = {"Config": {"Env": env or []}}
        self.commands = []

    def exec_run(self, command, **kwargs):
        self.commands.append(command)
        return self._exec_result


class _FakeClient:
    def __init__(self, containers):
        self._containers = containers

    class _Coll:
        def __init__(self, items):
            self._items = items

        def list(self, *a, **k):
            return self._items

    @property
    def containers(self):
        return self._Coll(self._containers)


def test_find_container_matches_substring_case_insensitively():
    target = _FakeContainer("PostgreSQL-18_pg-lmzvd06-ccc-01.1.abc")
    client = _FakeClient([_FakeContainer("traefik_traefik.1.x"), target])
    assert clusters.find_container(client, ("_pg-",)) is target


def test_find_container_returns_none_when_nothing_matches():
    client = _FakeClient([_FakeContainer("traefik_traefik.1.x")])
    assert clusters.find_container(client, ("_pg-",)) is None


def test_kafka_pattern_does_not_match_the_kafbat_ui():
    client = _FakeClient([_FakeContainer("kafbat-ui_kafbat-ui.1.x")])
    assert clusters.find_container(client, ("kafka_kafka-",)) is None


def test_exec_text_raises_on_nonzero_exit():
    container = _FakeContainer("x", exec_result=(1, b"boom"))
    try:
        clusters.exec_text(container, ["true"])
    except RuntimeError as exc:
        assert "boom" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_parse_pg_state_finds_primary_and_all_members():
    service = clusters.parse_pg_state(PG_STATE)
    assert service.kind == "postgres"
    assert service.reachable is True
    assert service.leader == "pg18-lmzvd06-ccn-02"
    assert len(service.members) == 5
    assert service.quorum_ok is True


def test_parse_pg_state_derives_node_names_and_lsn():
    service = clusters.parse_pg_state(PG_STATE)
    primary = service.members[0]
    assert primary.node == "lmzvd06-ccn-02"
    assert primary.role == "primary"
    assert primary.healthy is True
    assert primary.detail == "0/75243B8"
    assert primary.warning is None


def test_parse_pg_state_marks_a_secondary_whose_lsn_lags():
    lagging = PG_STATE.replace(
        "pg18-lmzvd06-ccn-03:5432 |   1: 0/75243B8", "pg18-lmzvd06-ccn-03:5432 |   1: 0/7000000"
    )
    service = clusters.parse_pg_state(lagging)
    behind = [m for m in service.members if m.name == "pg18-lmzvd06-ccn-03"][0]
    assert behind.warning == "lag"


def test_parse_pg_state_marks_a_member_in_transition():
    moving = PG_STATE.replace(
        "|           secondary |           secondary\npg18-lmzvd06-ccn-04",
        "|           secondary |             primary\npg18-lmzvd06-ccn-04",
    )
    service = clusters.parse_pg_state(moving)
    moving_member = [m for m in service.members if m.name == "pg18-lmzvd06-ccn-03"][0]
    assert moving_member.warning == "→ primary"


def test_parse_pg_state_reports_no_quorum_when_most_members_are_down():
    broken = "\n".join(PG_STATE.splitlines()[:4]) + "\n" + "\n".join(
        line.replace("secondary |           secondary", "draining  |           draining ")
        for line in PG_STATE.splitlines()[4:]
    )
    service = clusters.parse_pg_state(broken)
    assert service.quorum_ok is False


def test_probe_postgres_is_not_applicable_without_a_local_container():
    service = clusters.probe_postgres(_FakeClient([]))
    assert service.applicable is False
    assert service.error is None


def test_probe_postgres_runs_pg_autoctl_and_parses_it():
    container = _FakeContainer(
        "PostgreSQL-18_pg-lmzvd06-ccc-01.1.abc", exec_result=(0, PG_STATE.encode())
    )
    service = clusters.probe_postgres(_FakeClient([container]))
    assert container.commands == [["pg_autoctl", "show", "state"]]
    assert service.leader == "pg18-lmzvd06-ccn-02"


def test_probe_postgres_reports_an_exec_failure_as_error():
    container = _FakeContainer(
        "PostgreSQL-18_pg-lmzvd06-ccc-01.1.abc", exec_result=(1, b"connection refused")
    )
    service = clusters.probe_postgres(_FakeClient([container]))
    assert service.applicable is True
    assert service.reachable is False
    assert "connection refused" in service.error


def test_probe_postgres_reports_docker_api_failure_as_error():
    """Docker socket failure must be distinct from 'not applicable'."""
    class _FailingClient:
        class _FailingColl:
            def list(self, *a, **k):
                raise RuntimeError("Docker socket permission denied")

        @property
        def containers(self):
            return self._FailingColl()

    service = clusters.probe_postgres(_FailingClient())
    assert service.applicable is True  # Docker check was attempted
    assert service.error is not None  # But it failed
    assert "permission denied" in service.error


def test_collect_clusters_only_probes_the_requested_kinds():
    assert clusters.collect_clusters(_FakeClient([]), kinds=[]) == []
    result = clusters.collect_clusters(_FakeClient([]), kinds=["postgres"])
    assert [s.kind for s in result] == ["postgres"]
