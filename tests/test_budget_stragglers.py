"""The straggler registry must not grow without bound in follow mode.

Abandoned check threads are kept so `atexit` can give them one short grace
period. In a one-shot login run that list is emptied seconds later by the
interpreter exiting. Follow mode has no such end: it refreshes every 20
seconds for as long as the terminal is open, and on a node with a permanently
slow Docker or DNS the same check times out on every pass. Each timeout used
to append a thread object that nothing ever removed.
"""

from __future__ import annotations

import threading
import time

from terminal_status_panel import budget


def _drain_registry():
    with budget._stragglers_lock:
        budget._stragglers.clear()


def test_a_finished_straggler_is_not_kept(monkeypatch):
    """The whole point: a thread that ended after its deadline is not a straggler."""
    _drain_registry()
    release = threading.Event()

    def slow():
        release.wait(5.0)
        return "late"

    budget.run_with_budget({"slow": slow}, budget=0.05)
    with budget._stragglers_lock:
        assert len(budget._stragglers) == 1, "the abandoned thread should be registered"

    release.set()
    # Let the abandoned thread actually finish before the next run.
    for _ in range(100):
        with budget._stragglers_lock:
            alive = [t for t in budget._stragglers if t.is_alive()]
        if not alive:
            break
        time.sleep(0.01)

    # A second run must sweep the finished one rather than stack on top of it.
    budget.run_with_budget({"quick": lambda: "fine"}, budget=1.0)

    with budget._stragglers_lock:
        assert budget._stragglers == [], "a finished thread must not stay registered"
    _drain_registry()


def test_repeated_timeouts_do_not_grow_the_registry_without_bound():
    """Follow mode, compressed: many passes, each with one check that overruns."""
    _drain_registry()
    releases = []

    try:
        for _ in range(25):
            release = threading.Event()
            releases.append(release)

            def slow(release=release):
                release.wait(10.0)
                return "late"

            budget.run_with_budget({"slow": slow}, budget=0.02)
            release.set()
            # Give the thread a moment to actually end, as a real straggler would.
            time.sleep(0.02)

        with budget._stragglers_lock:
            registered = len(budget._stragglers)

        # Without the sweep this is 25 and climbing. A couple may legitimately
        # still be running: the assertion is that the list tracks *live*
        # threads, not that it is empty at an arbitrary instant.
        assert registered < 5, f"registry holds {registered} threads after 25 timed-out passes"
    finally:
        for release in releases:
            release.set()
        _drain_registry()


def test_a_live_straggler_is_still_kept():
    """The sweep must not throw away what the atexit grace period is for."""
    _drain_registry()
    release = threading.Event()

    def slow():
        release.wait(5.0)
        return "late"

    try:
        budget.run_with_budget({"slow": slow}, budget=0.05)
        budget.run_with_budget({"quick": lambda: "fine"}, budget=1.0)

        with budget._stragglers_lock:
            alive = [t for t in budget._stragglers if t.is_alive()]
        assert len(alive) == 1, "a still-running abandoned thread must stay registered"
    finally:
        release.set()
        _drain_registry()


def test_the_result_of_a_timed_out_check_is_still_truncated_not_failed():
    """The sweep must not disturb what the budget reports."""
    _drain_registry()
    release = threading.Event()

    def slow():
        release.wait(5.0)
        return "late"

    try:
        outcome = budget.run_with_budget({"slow": slow, "quick": lambda: "fine"}, budget=0.05)

        assert "slow" in outcome.truncated
        assert outcome.failed == {}
        assert outcome.results.get("quick") == "fine"
    finally:
        release.set()
        _drain_registry()
