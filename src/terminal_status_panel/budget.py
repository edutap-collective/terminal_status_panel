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


def run_with_budget(tasks: dict[str, Callable[[], Any]], budget: float) -> BudgetResult:
    """Run every callable in *tasks* concurrently, waiting at most *budget* seconds."""
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

    deadline = time.monotonic() + budget
    for _, thread in threads:
        thread.join(max(0.0, deadline - time.monotonic()))

    with lock:
        truncated = [
            name for name, _ in threads if name not in results and name not in failed
        ]
        return BudgetResult(results=dict(results), truncated=truncated, failed=dict(failed))
