"""Run named callables concurrently under a single wall-clock budget.

The only module in the package that touches concurrency. It exists so a hung
check degrades to "unknown" instead of delaying the login shell.

Deliberately hand-rolled daemon threads rather than ``ThreadPoolExecutor``:
the executor's workers are non-daemon and get joined by an ``atexit`` hook, so
a check that outlives the budget would still hold up interpreter exit — which
is exactly the failure this budget is meant to prevent.

A thread that is still inside ``subprocess.run`` when the interpreter exits is
frozen before its ``timeout`` can kill the child, and the child — ``sudo -n wg``
or ``sudo -n gluster`` — is reparented to init and keeps running. Harmless in
itself (both are read-only status commands), but this is a login path, so
``EXIT_GRACE_SECONDS`` buys such a thread a short, bounded moment at exit to
finish or to kill its own child. It is a mitigation, not a guarantee: a child
that outlives the grace is still orphaned. Making that impossible would mean
owning the ``Popen`` here and killing its process group, which buys little for
two short read-only commands and would move process handling out of the
collectors that know what they spawned.
"""

from __future__ import annotations

import atexit
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

# Bounded on purpose: this delay is paid by a login shell, and only when a
# check was already abandoned.
EXIT_GRACE_SECONDS = 0.25

_stragglers: list[threading.Thread] = []
_stragglers_lock = threading.Lock()


def _join_stragglers() -> None:
    """Give abandoned check threads one short, shared grace period at exit."""
    with _stragglers_lock:
        threads = [thread for thread in _stragglers if thread.is_alive()]
        _stragglers.clear()
    deadline = time.monotonic() + EXIT_GRACE_SECONDS
    for thread in threads:
        thread.join(max(0.0, deadline - time.monotonic()))


atexit.register(_join_stragglers)


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

    abandoned = [thread for _, thread in threads if thread.is_alive()]
    with _stragglers_lock:
        # Sweep on every run, not only on one that adds. A thread lands here
        # because it outlived its deadline, not because it will never end --
        # most finish a moment later, and once one has, holding its `Thread`
        # object buys the exit grace period nothing.
        #
        # Nothing else empties this list: `_join_stragglers` runs at
        # interpreter exit, which for a login run is seconds away and for
        # `--follow` never comes. A node with a permanently slow Docker or DNS
        # times the same check out on every 20-second pass, so without this the
        # list grows for as long as the terminal stays open.
        #
        # Sweeping unconditionally is what makes "this list holds the abandoned
        # threads still running" true after every call rather than only after a
        # timeout. The lock is uncontended and the list is a handful of entries.
        _stragglers[:] = [thread for thread in _stragglers if thread.is_alive()]
        _stragglers.extend(abandoned)

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
