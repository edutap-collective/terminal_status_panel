"""Tests for the per-member MongoDB probe.

`db.hello()` on one node lists the replica set's members but reports state only
for the primary and for the node answering. Every other member rendered `⬜` --
truthfully, but uselessly: a five-member set showed one measured row and four
blanks, where PostgreSQL showed five measured ones.

`hello` is permitted before authentication, so the panel can ask each member
directly without credentials. `replSetGetStatus`, which would answer in one
round trip, cannot: measured against the real set it replies
`Command replSetGetStatus requires authentication`.

The timings quoted below were measured on a cluster node, not estimated:
a member answers in 171-279 ms, an unreachable one costs 97 ms (refused),
113 ms (does not resolve) or 787 ms (blackhole, bounded by the 700 ms
serverSelectionTimeoutMS the command sets).
"""

from __future__ import annotations

import json

from terminal_status_panel.collectors import clusters

FANOUT = json.dumps(
    {
        "set": "lrz_app",
        "me": "mongodb-app-1:27017",
        "primary": "mongodb-app-1:27017",
        "hosts": [f"mongodb-app-{n}:27017" for n in range(1, 6)],
        "members": [
            {"host": "mongodb-app-1:27017", "state": "primary"},
            {"host": "mongodb-app-2:27017", "state": "secondary"},
            {"host": "mongodb-app-3:27017", "state": "secondary"},
            {"host": "mongodb-app-4:27017", "state": "secondary"},
            {"host": "mongodb-app-5:27017", "state": "secondary"},
        ],
    }
)


def _by_name(service):
    return {member.name: member for member in service.members}


# --------------------------------------------------------------------------- #
# The point of the change
# --------------------------------------------------------------------------- #


def test_every_member_reached_reports_its_own_state():
    service = clusters.parse_mongo_hello(FANOUT)

    members = _by_name(service)
    assert members["mongodb-app-1:27017"].role == "primary"
    assert all(members[f"mongodb-app-{n}:27017"].role == "secondary" for n in (2, 3, 4, 5))
    assert all(member.healthy is True for member in service.members)


def test_an_unreachable_member_is_measured_broken_not_unknown():
    """Reached and refusing is a fact. It must not read the same as unmeasured."""
    payload = json.loads(FANOUT)
    payload["members"][3]["state"] = "unreachable"

    service = clusters.parse_mongo_hello(json.dumps(payload))

    member = _by_name(service)["mongodb-app-4:27017"]
    assert member.healthy is False
    assert member.role == "unreachable"


def test_a_member_the_deadline_cut_off_stays_unmeasured():
    """The partial answer: what was reached is reported, the rest is blank.

    This is the degraded path and it must never be worse than the old
    behaviour -- which is exactly what a member with no entry produces.
    """
    payload = json.loads(FANOUT)
    payload["members"] = payload["members"][:2]

    service = clusters.parse_mongo_hello(json.dumps(payload))

    members = _by_name(service)
    assert len(service.members) == 5, "every configured member still gets a row"
    assert members["mongodb-app-1:27017"].healthy is True
    assert members["mongodb-app-2:27017"].healthy is True
    for n in (3, 4, 5):
        assert members[f"mongodb-app-{n}:27017"].healthy is None
        assert members[f"mongodb-app-{n}:27017"].role == "member"


def test_an_arbiter_is_listed_even_though_hosts_omits_it():
    """`hello` reports arbiters in their own field, not in `hosts`.

    The set on the cluster has none today -- measured, the field is absent --
    but a set that has one would have shown a member short, with no hint that
    a vote was missing from the display.
    """
    payload = json.loads(FANOUT)
    payload["arbiters"] = ["mongodb-arb-1:27017"]
    payload["members"].append({"host": "mongodb-arb-1:27017", "state": "arbiter"})

    service = clusters.parse_mongo_hello(json.dumps(payload))

    member = _by_name(service)["mongodb-arb-1:27017"]
    assert member.role == "arbiter"
    assert member.healthy is True


# --------------------------------------------------------------------------- #
# Backwards compatibility: the old single-hello answer still parses
# --------------------------------------------------------------------------- #


def test_an_answer_without_a_members_array_reads_as_before():
    """A mongosh that produced no fan-out must not read as five dead members."""
    payload = json.loads(FANOUT)
    del payload["members"]
    payload["isPrimary"] = True

    service = clusters.parse_mongo_hello(json.dumps(payload))

    members = _by_name(service)
    assert members["mongodb-app-1:27017"].healthy is True
    assert members["mongodb-app-2:27017"].healthy is None
    assert members["mongodb-app-2:27017"].role == "member"


# --------------------------------------------------------------------------- #
# The command the probe builds
# --------------------------------------------------------------------------- #


def test_the_fanout_budget_leaves_room_for_mongosh_to_start():
    """The JS deadline has to fit inside the budget thread's own timeout.

    mongosh itself takes 0.97-1.50 s to start on a cluster node before a line
    of the script runs. A deadline set to the full timeout would be reached
    only after the budget had already abandoned the check, and the panel would
    show a truncated MongoDB block instead of a partial one.
    """
    budget = clusters._mongo_fanout_budget(6.0)

    assert budget < 6.0 - clusters.MONGO_STARTUP_ALLOWANCE + 0.001
    assert budget > 0


def test_a_timeout_too_small_for_a_fanout_still_probes_the_local_member():
    """Never negative, and never zero: the local hello still has to happen."""
    assert clusters._mongo_fanout_budget(0.5) > 0
    assert clusters._mongo_fanout_budget(0.0) > 0


def test_the_command_carries_the_budget_and_a_per_member_timeout():
    command = clusters.mongo_command(6.0)

    script = command[-1]
    assert "serverSelectionTimeoutMS=700" in script
    assert str(int(clusters._mongo_fanout_budget(6.0) * 1000)) in script
    assert "--tls" in command


def test_the_command_never_writes():
    """A status panel reads. Nothing in this script may change the set."""
    script = clusters.mongo_command(6.0)[-1]

    for forbidden in ("rs.add", "rs.remove", "rs.reconfig", "rs.stepDown", "insert", "drop"):
        assert forbidden not in script
