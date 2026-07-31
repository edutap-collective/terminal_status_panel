"""Run named callables concurrently under a single wall-clock budget.

The only module in the package that touches concurrency. It exists so a hung
check degrades to "unknown" instead of delaying the login shell.

Deliberately hand-rolled daemon threads rather than ``ThreadPoolExecutor``:
the executor's workers are non-daemon and get joined by an ``atexit`` hook, so
a check that outlives the budget would still hold up interpreter exit — which
is exactly the failure this budget is meant to prevent.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class BudgetResult:
    """Outcome of one budgeted run.

    ``truncated`` and ``failed`` are kept apart on purpose: a blown budget says
    nothing about the state of the checked service, while a raised exception
    does. Conflating them would be the worst property of a status panel.
    """

    results: dict[str, Any] = field(default_factory=dict)
    truncated: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)


def run_with_budget(
    tasks: dict[str, Callable[[], Any]],
    budget: float,
    timeouts: dict[str, float] | None = None,
) -> BudgetResult:
    """Run every callable in *tasks* concurrently under one wall-clock budget.

    *timeouts* optionally gives a task its own, shorter deadline. A task that
    has not finished by then is reported as truncated even though budget is
    left — that is what makes a per-check timeout mean anything: the checks that
    did finish are reported while the slow one is named as unfinished, instead
    of one hung probe deciding the outcome for all of them.

    A late answer from an expired task is discarded rather than reported, so the
    same run always produces the same verdict for it.
    """
    results: dict[str, Any] = {}
    failed: dict[str, str] = {}
    lock = threading.Lock()

    def runner(name: str, func: Callable[[], Any]) -> None:
        try:
            value = func()
        except Exception as exc:  # a collector should catch its own, but never trust that
            with lock:
                failed[name] = f"{type(exc).__name__}: {exc}"
            return
        with lock:
            results[name] = value

    threads: list[tuple[str, threading.Thread]] = []
    for name, func in tasks.items():
        thread = threading.Thread(
            target=runner, args=(name, func), daemon=True, name=f"check-{name}"
        )
        thread.start()
        threads.append((name, thread))

    started = time.monotonic()
    budget_deadline = started + budget
    deadlines = {
        name: min(budget_deadline, started + (timeouts or {}).get(name, budget))
        for name, _ in threads
    }

    # Join in deadline order, so every task is waited for exactly as long as it
    # is entitled to and no task's overrun eats another's waiting time.
    expired: set[str] = set()
    for name, thread in sorted(threads, key=lambda item: deadlines[item[0]]):
        thread.join(max(0.0, deadlines[name] - time.monotonic()))
        if thread.is_alive():
            expired.add(name)

    with lock:
        truncated = [
            name
            for name, _ in threads
            if name in expired or (name not in results and name not in failed)
        ]
        return BudgetResult(
            results={k: v for k, v in results.items() if k not in expired},
            truncated=truncated,
            failed={k: v for k, v in failed.items() if k not in expired},
        )
