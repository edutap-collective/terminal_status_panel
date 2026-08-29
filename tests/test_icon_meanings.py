"""One test per meaning, because five of them once shared a glyph.

`⬜` came to stand for a measured warning, a service deliberately scaled to
zero, a job with no history, a cluster with no quorum reading, and a check that
never ran. Three of those are genuinely "nobody knows"; two are things the
panel measured and then reported as if it had not.

The collapse was invisible while the glyph was `·`, which read as a neutral
bullet. Giving it a meaning made the contradiction visible -- so these tests
pin the meanings apart rather than the character, and a future glyph change
costs nothing here.
"""

from __future__ import annotations

from rich.console import Console

from terminal_status_panel.config import Config
from terminal_status_panel.model import (
    ClusterService,
    HealthInfo,
    PeerReachability,
    ServiceStatus,
)
from terminal_status_panel.render import icons
from terminal_status_panel.render.health import health_section
from terminal_status_panel.render.verdict import service_verdict


def _text(renderable, width: int = 120) -> str:
    console = Console(width=width, force_terminal=False, color_system=None)
    with console.capture() as capture:
        console.print(renderable)
    return capture.get()


def _verdict(running: int, desired: int, **kwargs) -> str:
    return service_verdict(
        [ServiceStatus("svc", running, desired, stack="app")], node_count=1, **kwargs
    ).plain


# --------------------------------------------------------------------------- #
# Measured, and therefore never `⬜`
# --------------------------------------------------------------------------- #


def test_scaled_to_zero_is_paused():
    """`0/0` is an answer from the daemon, not a missing one."""
    assert icons.PAUSED in _verdict(0, 0)
    assert icons.UNKNOWN not in _verdict(0, 0)


def test_mixed_endpoint_families_is_a_warning():
    """A measured finding on a yellow line takes the warning glyph.

    It reports the precondition under which a conntrack-dependent tunnel stops
    working. Prefixing that with "nothing was observed" contradicted both the
    colour and the sentence.
    """
    health = HealthInfo(
        peers_probed=True,
        peers=[
            # `family` is what the rule counts -- an endpoint string alone
            # holds no opinion. The first version of this test set only the
            # endpoint, produced no warning line at all, and passed on an
            # `if` that was never true.
            PeerReachability(name="a", method="wg", ok=True, family="IPv4"),
            PeerReachability(name="b", method="wg", ok=True, family="IPv6"),
        ],
    )

    out = _text(health_section(health, Config()))

    assert "mixed endpoint families" in out, "the fixture must actually trigger the warning"
    line = next(line for line in out.splitlines() if "mixed endpoint families" in line)
    assert icons.WARN in line
    assert icons.UNKNOWN not in line


# --------------------------------------------------------------------------- #
# Genuinely unmeasured, and therefore still `⬜`
# --------------------------------------------------------------------------- #


def test_a_cluster_without_a_quorum_reading_stays_unmeasured():
    verdict = service_verdict(
        [ServiceStatus("svc", 1, 1, stack="app")],
        kind="mongodb",
        cluster=ClusterService(kind="mongodb", reachable=True, quorum_ok=None),
        node_count=1,
    ).plain

    assert icons.UNKNOWN in verdict
    assert icons.PAUSED not in verdict


def test_a_check_that_never_ran_stays_unmeasured():
    out = _text(health_section(HealthInfo(), Config()))

    assert icons.UNKNOWN in out


# --------------------------------------------------------------------------- #
# The vocabulary itself
# --------------------------------------------------------------------------- #


def test_paused_and_unknown_are_different_glyphs():
    """The whole point. One is a decision, the other is an absence."""
    assert icons.PAUSED != icons.UNKNOWN


def test_paused_is_gentler_than_a_warning_but_visible():
    """Ranked so a paused service neither raises an alarm nor disappears.

    In a row mixing states the more severe one wins, and `⏸️` must lose to a
    real warning while still outranking a plain ✅ -- a reader scanning a
    column should notice that something is deliberately off.
    """
    from terminal_status_panel.render.verdict import severity

    assert severity(icons.OK) < severity(icons.PAUSED) <= severity(icons.UNKNOWN)
    assert severity(icons.PAUSED) < severity(icons.WARN)
