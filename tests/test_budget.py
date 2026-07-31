import time

from terminal_status_panel.budget import run_with_budget


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
