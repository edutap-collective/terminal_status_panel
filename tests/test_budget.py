import time

from terminal_status_panel import budget as budget_module
from terminal_status_panel.budget import run_with_budget


def test_an_abandoned_check_gets_a_short_grace_at_interpreter_exit():
    """A thread frozen inside subprocess.run at exit leaves its child running
    as an orphan. The grace lets it finish — or kill its child — first."""
    finished = []

    def slow():
        time.sleep(0.15)
        finished.append(True)

    run_with_budget({"slow": slow}, budget=0.01)
    assert finished == []  # abandoned, as the budget requires
    budget_module._join_stragglers()  # what the atexit hook calls
    assert finished == [True]


def test_the_exit_grace_is_bounded():
    """It is paid by a login shell, so it must never become a wait."""
    def endless():
        time.sleep(5)

    run_with_budget({"endless": endless}, budget=0.01)
    started = time.monotonic()
    budget_module._join_stragglers()
    assert time.monotonic() - started < budget_module.EXIT_GRACE_SECONDS + 0.2


def test_a_completed_run_leaves_nothing_to_wait_for():
    run_with_budget({"fast": lambda: 1}, budget=1.0)
    started = time.monotonic()
    budget_module._join_stragglers()
    assert time.monotonic() - started < 0.05


def test_fast_tasks_all_complete():
    result = run_with_budget({"a": lambda: 1, "b": lambda: 2}, budget=2.0)
    assert result.results == {"a": 1, "b": 2}
    assert result.truncated == []
    assert result.failed == {}


def test_slow_task_is_reported_as_truncated_not_failed():
    def slow():
        time.sleep(5)
        return "too late"

    result = run_with_budget({"fast": lambda: "ok", "slow": slow}, budget=0.3)
    assert result.results == {"fast": "ok"}
    assert result.truncated == ["slow"]
    assert result.failed == {}


def test_budget_bounds_wall_clock_not_the_sum():
    def slow():
        time.sleep(5)

    started = time.monotonic()
    run_with_budget({"a": slow, "b": slow, "c": slow}, budget=0.3)
    elapsed = time.monotonic() - started
    assert elapsed < 1.5, f"budget overrun: {elapsed:.2f}s"


def test_a_task_is_truncated_at_its_own_timeout_while_the_others_run_on():
    """A per-task timeout only means something if the task that overruns it is
    the only one that loses its result."""
    def slow():
        time.sleep(5)
        return "too late"

    def steady():
        time.sleep(0.4)
        return "ok"

    started = time.monotonic()
    result = run_with_budget(
        {"slow": slow, "steady": steady}, budget=3.0, timeouts={"slow": 0.1}
    )
    elapsed = time.monotonic() - started
    assert result.results == {"steady": "ok"}
    assert result.truncated == ["slow"]
    assert elapsed < 1.5, f"the steady task should not have waited: {elapsed:.2f}s"


def test_a_task_without_its_own_timeout_gets_the_whole_budget():
    def steady():
        time.sleep(0.2)
        return "ok"

    result = run_with_budget({"steady": steady}, budget=2.0, timeouts={"other": 0.01})
    assert result.results == {"steady": "ok"}


def test_raising_task_is_reported_as_failed_not_truncated():
    def boom():
        raise ValueError("kaputt")

    result = run_with_budget({"boom": boom}, budget=1.0)
    assert result.results == {}
    assert result.truncated == []
    assert "boom" in result.failed
    assert "kaputt" in result.failed["boom"]


def test_worker_threads_are_daemon_so_they_never_block_interpreter_exit():
    import threading

    seen = {}

    def record():
        seen["daemon"] = threading.current_thread().daemon
        return None

    run_with_budget({"record": record}, budget=1.0)
    assert seen["daemon"] is True
