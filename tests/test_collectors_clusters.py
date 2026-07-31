import subprocess

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


def test_collect_clusters_reports_unknown_kinds_as_error():
    result = clusters.collect_clusters(_FakeClient([]), kinds=["nosuchthing"])
    assert len(result) == 1
    assert result[0].kind == "nosuchthing"
    assert result[0].error == "unknown kind"


MONGO_HELLO = (
    '{"set":"lrz_app","me":"mongodb-lmzvd06-internet-app-1:27017",'
    '"primary":"mongodb-lmzvd06-internet-app-2:27017","isPrimary":false,'
    '"hosts":["mongodb-lmzvd06-internet-app-1:27017","mongodb-lmzvd06-internet-app-2:27017",'
    '"mongodb-lmzvd06-internet-app-3:27017","mongodb-lmzvd06-internet-app-4:27017",'
    '"mongodb-lmzvd06-internet-app-5:27017"]}\n'
)


def test_parse_mongo_hello_reads_set_name_and_primary():
    service = clusters.parse_mongo_hello(MONGO_HELLO)
    assert service.kind == "mongodb"
    assert service.name == "lrz_app"
    assert service.reachable is True
    assert service.leader == "mongodb-lmzvd06-internet-app-2:27017"
    assert service.quorum_ok is True
    assert len(service.members) == 5


def test_parse_mongo_hello_only_claims_health_where_it_has_evidence():
    service = clusters.parse_mongo_hello(MONGO_HELLO)
    by_name = {member.name: member for member in service.members}
    # We just talked to this one, and the set agrees on the primary.
    assert by_name["mongodb-lmzvd06-internet-app-1:27017"].healthy is True
    assert by_name["mongodb-lmzvd06-internet-app-2:27017"].healthy is True
    assert by_name["mongodb-lmzvd06-internet-app-2:27017"].role == "primary"
    # db.hello() says nothing about the state of the others.
    assert by_name["mongodb-lmzvd06-internet-app-4:27017"].healthy is None
    assert by_name["mongodb-lmzvd06-internet-app-4:27017"].role == "member"


def test_parse_mongo_hello_without_a_primary_has_no_quorum():
    service = clusters.parse_mongo_hello(MONGO_HELLO.replace(
        '"primary":"mongodb-lmzvd06-internet-app-2:27017",', ""
    ))
    assert service.leader is None
    assert service.quorum_ok is False


def test_probe_mongodb_is_not_applicable_without_a_local_container():
    service = clusters.probe_mongodb(_FakeClient([]))
    assert service.applicable is False
    assert service.error is None


def test_probe_mongodb_runs_mongosh_unauthenticated():
    container = _FakeContainer(
        "mongodb_lmzvd06-internet-app-1.1.abc", exec_result=(0, MONGO_HELLO.encode())
    )
    service = clusters.probe_mongodb(_FakeClient([container]))
    command = container.commands[0]
    assert command[0] == "mongosh"
    assert "--quiet" in command
    assert not any("-u" == part or "--username" in part for part in command)
    assert service.name == "lrz_app"


def test_probe_mongodb_reports_docker_api_failure_as_error():
    """Docker socket failure must be distinct from 'not applicable'."""
    class _FailingClient:
        class _FailingColl:
            def list(self, *a, **k):
                raise RuntimeError("Docker socket permission denied")

        @property
        def containers(self):
            return self._FailingColl()

    service = clusters.probe_mongodb(_FailingClient())
    assert service.applicable is True  # Docker check was attempted
    assert service.error is not None  # But it failed
    assert "permission denied" in service.error


KAFKA_QUORUM = """\
ClusterId:              Jucv8gBrQg-WOxKNTIAPVw
LeaderId:               1
LeaderEpoch:            2
HighWatermark:          173016
MaxFollowerLag:         0
MaxFollowerLagTimeMs:   296
CurrentVoters:          [{"id": 0, "endpoints": ["CONTROLLER://kafka-lmzvd06-ccc-01:9093"]}, {"id": 1, "endpoints": ["CONTROLLER://kafka-lmzvd06-ccn-01:9093"]}, {"id": 2, "endpoints": ["CONTROLLER://kafka-lmzvd06-ccn-02:9093"]}, {"id": 3, "endpoints": ["CONTROLLER://kafka-lmzvd06-ccn-03:9093"]}, {"id": 4, "endpoints": ["CONTROLLER://kafka-lmzvd06-ccn-04:9093"]}]
CurrentObservers:       []
"""


def test_parse_kafka_quorum_maps_the_leader_id_to_its_endpoint_host():
    service = clusters.parse_kafka_quorum(KAFKA_QUORUM)
    assert service.kind == "kafka"
    assert service.reachable is True
    assert service.leader == "kafka-lmzvd06-ccn-01"
    assert service.quorum_ok is True
    assert service.name == "Jucv8gBrQg-WOxKNTIAPVw"


def test_parse_kafka_quorum_lists_voters_with_the_leader_marked():
    service = clusters.parse_kafka_quorum(KAFKA_QUORUM)
    assert len(service.members) == 5
    by_name = {member.name: member for member in service.members}
    assert by_name["kafka-lmzvd06-ccn-01"].role == "leader"
    assert by_name["kafka-lmzvd06-ccc-01"].role == "voter"
    assert by_name["kafka-lmzvd06-ccc-01"].healthy is True


def test_parse_kafka_quorum_carries_follower_lag_as_service_detail():
    service = clusters.parse_kafka_quorum(KAFKA_QUORUM)
    assert "0" in service.detail
    assert "296" in service.detail


def test_parse_kafka_quorum_without_a_leader_has_no_quorum():
    service = clusters.parse_kafka_quorum(KAFKA_QUORUM.replace("LeaderId:               1", ""))
    assert service.leader is None
    assert service.quorum_ok is False


def test_parse_kafka_quorum_marks_observers():
    with_observer = KAFKA_QUORUM.replace(
        "CurrentObservers:       []",
        'CurrentObservers:       [{"id": 9, "endpoints": ["CONTROLLER://kafka-obs:9093"]}]',
    )
    service = clusters.parse_kafka_quorum(with_observer)
    by_name = {member.name: member for member in service.members}
    assert by_name["kafka-obs"].role == "observer"


def test_probe_kafka_uses_the_absolute_tool_path_and_the_mounted_client_properties():
    container = _FakeContainer(
        "kafka_kafka-lmzvd06-ccc-01.1.abc", exec_result=(0, KAFKA_QUORUM.encode())
    )
    service = clusters.probe_kafka(_FakeClient([container]))
    command = container.commands[0]
    assert command[0] == "/opt/kafka/bin/kafka-metadata-quorum.sh"
    assert "/client.properties" in command
    assert service.leader == "kafka-lmzvd06-ccn-01"


def test_probe_kafka_is_not_applicable_without_a_local_broker():
    service = clusters.probe_kafka(_FakeClient([]))
    assert service.applicable is False
    assert service.error is None


def test_probe_kafka_reports_docker_api_failure_as_error():
    """Docker socket failure must be distinct from 'not applicable'."""
    class _FailingClient:
        class _FailingColl:
            def list(self, *a, **k):
                raise RuntimeError("Docker socket permission denied")

        @property
        def containers(self):
            return self._FailingColl()

    service = clusters.probe_kafka(_FailingClient())
    assert service.applicable is True  # Docker check was attempted
    assert service.error is not None  # But it failed
    assert "permission denied" in service.error


GLUSTER_PEERS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cliOutput>
  <opRet>0</opRet>
  <peerStatus>
    <peer><uuid>e309</uuid><hostname>wg-lmzvd06-ccn-01.srv.mwn.de</hostname>
      <connected>1</connected><stateStr>Peer in Cluster</stateStr></peer>
    <peer><uuid>f72d</uuid><hostname>wg-lmzvd06-ccn-02.srv.mwn.de</hostname>
      <connected>0</connected><stateStr>Peer in Cluster</stateStr></peer>
  </peerStatus>
</cliOutput>
"""

GLUSTER_VOLUME = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cliOutput>
  <opRet>0</opRet>
  <volStatus><volumes><volume>
    <volName>shared</volName>
    <nodeCount>4</nodeCount>
    <node><hostname>wg-lmzvd06-ccc-01.srv.mwn.de</hostname>
      <path>/data/glusterfs/brick1/shared</path><status>1</status></node>
    <node><hostname>wg-lmzvd06-ccn-01.srv.mwn.de</hostname>
      <path>/data/glusterfs/brick1/shared</path><status>0</status></node>
    <node><hostname>Self-heal Daemon</hostname><path>localhost</path><status>1</status></node>
    <node><hostname>Self-heal Daemon</hostname>
      <path>wg-lmzvd06-ccn-01.srv.mwn.de</path><status>1</status></node>
  </volume></volumes></volStatus>
</cliOutput>
"""


def test_parse_gluster_excludes_self_heal_daemons_from_the_brick_count():
    service = clusters.parse_gluster(GLUSTER_PEERS, GLUSTER_VOLUME)
    bricks = [member for member in service.members if member.role == "brick"]
    assert len(bricks) == 2, "self-heal daemons must not be counted as bricks"


def test_parse_gluster_reports_volume_name_and_is_leaderless():
    service = clusters.parse_gluster(GLUSTER_PEERS, GLUSTER_VOLUME)
    assert service.kind == "glusterfs"
    assert service.name == "shared"
    assert service.leader is None
    assert service.reachable is True


def test_parse_gluster_marks_a_disconnected_peer_and_an_offline_brick():
    service = clusters.parse_gluster(GLUSTER_PEERS, GLUSTER_VOLUME)
    by_key = {(member.role, member.name): member for member in service.members}
    assert by_key[("peer", "wg-lmzvd06-ccn-01.srv.mwn.de")].healthy is True
    assert by_key[("peer", "wg-lmzvd06-ccn-02.srv.mwn.de")].healthy is False
    assert by_key[("brick", "wg-lmzvd06-ccn-01.srv.mwn.de")].healthy is False


def test_parse_gluster_has_no_quorum_when_most_peers_are_disconnected():
    two_down = GLUSTER_PEERS.replace(
        "<hostname>wg-lmzvd06-ccn-01.srv.mwn.de</hostname>\n      <connected>1</connected>",
        "<hostname>wg-lmzvd06-ccn-01.srv.mwn.de</hostname>\n      <connected>0</connected>",
    )
    assert clusters.parse_gluster(two_down, GLUSTER_VOLUME).quorum_ok is False


# `gluster volume status --xml` with no volume name (as we call it) returns
# *every* volume on the host as sibling <volume> elements. This fixture adds
# a second volume ("backup") whose bricks must not leak into the reported
# members of the first volume ("shared").
GLUSTER_VOLUME_MULTI = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cliOutput>
  <opRet>0</opRet>
  <volStatus><volumes>
  <volume>
    <volName>shared</volName>
    <nodeCount>4</nodeCount>
    <node><hostname>wg-lmzvd06-ccc-01.srv.mwn.de</hostname>
      <path>/data/glusterfs/brick1/shared</path><status>1</status></node>
    <node><hostname>wg-lmzvd06-ccn-01.srv.mwn.de</hostname>
      <path>/data/glusterfs/brick1/shared</path><status>0</status></node>
    <node><hostname>Self-heal Daemon</hostname><path>localhost</path><status>1</status></node>
    <node><hostname>Self-heal Daemon</hostname>
      <path>wg-lmzvd06-ccn-01.srv.mwn.de</path><status>1</status></node>
  </volume>
  <volume>
    <volName>backup</volName>
    <nodeCount>2</nodeCount>
    <node><hostname>wg-lmzvd06-ccc-01.srv.mwn.de</hostname>
      <path>/data/glusterfs/brick1/backup</path><status>1</status></node>
    <node><hostname>wg-lmzvd06-ccn-01.srv.mwn.de</hostname>
      <path>/data/glusterfs/brick1/backup</path><status>1</status></node>
  </volume>
  </volumes></volStatus>
</cliOutput>
"""


def test_parse_gluster_does_not_leak_bricks_from_a_second_volume():
    service = clusters.parse_gluster(GLUSTER_PEERS, GLUSTER_VOLUME_MULTI)
    bricks = [member for member in service.members if member.role == "brick"]
    assert len(bricks) == 2, "only the first volume's bricks may be reported"
    assert all("backup" not in (member.detail or "") for member in bricks)


def test_parse_gluster_notes_additional_volumes_in_detail():
    service = clusters.parse_gluster(GLUSTER_PEERS, GLUSTER_VOLUME_MULTI)
    assert service.name == "shared"
    assert service.detail is not None
    assert "shared" in service.detail
    assert "1" in service.detail  # one additional volume beyond "shared"


def test_parse_gluster_single_volume_gets_no_extra_volumes_suffix():
    service = clusters.parse_gluster(GLUSTER_PEERS, GLUSTER_VOLUME)
    assert service.detail is None


def test_probe_glusterfs_is_not_applicable_without_passwordless_sudo(monkeypatch):
    class _Completed:
        def __init__(self, returncode, stdout="", stderr=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def refuse(*args, **kwargs):
        return _Completed(1, stderr="sudo: a password is required")

    monkeypatch.setattr(clusters.subprocess, "run", refuse)
    service = clusters.probe_glusterfs()
    assert service.applicable is False
    assert service.error is None


def test_probe_glusterfs_is_not_applicable_when_sudo_binary_is_missing(monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError("sudo")

    monkeypatch.setattr(clusters.subprocess, "run", missing)
    service = clusters.probe_glusterfs()
    assert service.applicable is False
    assert service.error is None


def test_probe_glusterfs_reports_a_broken_daemon_as_an_error(monkeypatch):
    class _Completed:
        returncode = 1
        stdout = ""
        stderr = "Connection failed. Please check if gluster daemon is operational."

    def broken(*args, **kwargs):
        return _Completed()

    monkeypatch.setattr(clusters.subprocess, "run", broken)
    service = clusters.probe_glusterfs()
    assert service.applicable is True
    assert service.error is not None
    assert "Connection failed" in service.error


def test_probe_glusterfs_reports_a_timeout_as_an_error(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="gluster", timeout=1.0)

    monkeypatch.setattr(clusters.subprocess, "run", timeout)
    service = clusters.probe_glusterfs()
    assert service.applicable is True
    assert service.error is not None


def test_probe_glusterfs_calls_sudo_n_with_xml(monkeypatch):
    calls = []

    class _Completed:
        returncode = 0
        stderr = ""

        def __init__(self, stdout):
            self.stdout = stdout

    def fake_run(command, **kwargs):
        calls.append(command)
        return _Completed(GLUSTER_PEERS if "peer" in command else GLUSTER_VOLUME)

    monkeypatch.setattr(clusters.subprocess, "run", fake_run)
    service = clusters.probe_glusterfs()
    assert calls[0][:3] == ["sudo", "-n", "gluster"]
    assert "--xml" in calls[0]
    assert service.name == "shared"


def test_rustfs_endpoints_treats_a_plain_path_as_a_single_local_instance():
    container = _FakeContainer("rustfs_rustfs.1.abc", env=["RUSTFS_VOLUMES=/data"])
    assert clusters.rustfs_endpoints(container) == ["https://localhost:9000"]


def test_rustfs_endpoints_reads_distributed_urls():
    container = _FakeContainer(
        "rustfs_rustfs.1.abc",
        env=["RUSTFS_VOLUMES=https://rustfs-a:9000/data https://rustfs-b:9000/data"],
    )
    assert clusters.rustfs_endpoints(container) == [
        "https://rustfs-a:9000",
        "https://rustfs-b:9000",
    ]


def test_rustfs_endpoints_falls_back_to_localhost_without_the_variable():
    container = _FakeContainer("rustfs_rustfs.1.abc", env=[])
    assert clusters.rustfs_endpoints(container) == ["https://localhost:9000"]


def test_probe_rustfs_is_not_applicable_without_a_local_container():
    service = clusters.probe_rustfs(_FakeClient([]))
    assert service.applicable is False
    assert service.error is None


def test_probe_rustfs_marks_a_200_endpoint_healthy():
    container = _FakeContainer(
        "rustfs_rustfs.1.abc", exec_result=(0, b"200"), env=["RUSTFS_VOLUMES=/data"]
    )
    service = clusters.probe_rustfs(_FakeClient([container]))
    assert service.kind == "rustfs"
    assert service.reachable is True
    assert service.leader is None
    assert len(service.members) == 1
    assert service.members[0].healthy is True
    assert service.members[0].role == "peer"
    assert "/health" in " ".join(container.commands[0])


def test_probe_rustfs_marks_a_non_200_endpoint_unhealthy():
    container = _FakeContainer(
        "rustfs_rustfs.1.abc", exec_result=(0, b"403"), env=["RUSTFS_VOLUMES=/data"]
    )
    service = clusters.probe_rustfs(_FakeClient([container]))
    assert service.members[0].healthy is False
    assert service.reachable is False
    assert service.quorum_ok is False


def test_probe_rustfs_reports_docker_api_failure_as_error():
    """Docker socket failure must be distinct from 'not applicable'."""
    class _FailingClient:
        class _FailingColl:
            def list(self, *a, **k):
                raise RuntimeError("Docker socket permission denied")

        @property
        def containers(self):
            return self._FailingColl()

    service = clusters.probe_rustfs(_FailingClient())
    assert service.applicable is True  # Docker check was attempted
    assert service.error is not None  # But it failed
    assert "permission denied" in service.error


def test_probe_rustfs_all_endpoints_failing_yields_not_reachable():
    """All endpoints down or unreachable → reachable=False, quorum_ok=False."""
    container = _FakeContainer(
        "rustfs_rustfs.1.abc",
        exec_result=(1, b"Connection refused"),
        env=["RUSTFS_VOLUMES=/data"],
    )
    service = clusters.probe_rustfs(_FakeClient([container]))
    assert service.reachable is False
    assert service.quorum_ok is False
    assert service.detail == "0/1 live"


def test_probe_rustfs_majority_endpoints_healthy_yields_quorum():
    """2/3 endpoints healthy → reachable=True, quorum_ok=True (majority)."""
    # Simulate three endpoints: two answer 200, one answers 500.
    # We'll create a container that tracks each call and returns different
    # status codes.
    class _MultiEndpointContainer:
        def __init__(self):
            self.name = "rustfs_rustfs.1.abc"
            self.attrs = {"Config": {"Env": [
                "RUSTFS_VOLUMES=https://rustfs-a:9000/data https://rustfs-b:9000/data https://rustfs-c:9000/data"
            ]}}
            self.call_count = 0
            self.commands = []

        def exec_run(self, command, **kwargs):
            self.commands.append(command)
            # Return 200 for the first two calls (endpoints a, b), 500 for the third (endpoint c).
            if self.call_count < 2:
                self.call_count += 1
                return (0, b"200")
            self.call_count += 1
            return (0, b"500")

    container = _MultiEndpointContainer()
    service = clusters.probe_rustfs(_FakeClient([container]))
    assert service.reachable is True
    assert service.quorum_ok is True
    assert service.detail == "2/3 live"


def test_probe_rustfs_minority_endpoints_healthy_loses_quorum():
    """1/3 endpoints healthy → reachable=True, quorum_ok=False (not majority)."""
    class _MultiEndpointContainer:
        def __init__(self):
            self.name = "rustfs_rustfs.1.abc"
            self.attrs = {"Config": {"Env": [
                "RUSTFS_VOLUMES=https://rustfs-a:9000/data https://rustfs-b:9000/data https://rustfs-c:9000/data"
            ]}}
            self.call_count = 0
            self.commands = []

        def exec_run(self, command, **kwargs):
            self.commands.append(command)
            # Return 200 for the first call only (endpoint a), 500 for b and c.
            if self.call_count == 0:
                self.call_count += 1
                return (0, b"200")
            self.call_count += 1
            return (0, b"500")

    container = _MultiEndpointContainer()
    service = clusters.probe_rustfs(_FakeClient([container]))
    assert service.reachable is True
    assert service.quorum_ok is False
    assert service.detail == "1/3 live"
