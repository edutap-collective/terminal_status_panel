import os
from types import SimpleNamespace

import psutil

from terminal_status_panel.collectors import processes
from terminal_status_panel.model import ProcessInfo


def _write_cgroup(tmp_path, pid: int, line: str) -> None:
    target = tmp_path / str(pid)
    target.mkdir()
    (target / "cgroup").write_text(line)


class _FakeProcess:
    """Stands in for `psutil.Process`, offering only the methods
    `collect_processes` actually calls -- priming (`cpu_percent`), and the
    four per-row reads (`name`, `cpu_percent` again, `memory_percent`,
    `memory_info`).

    `vanishes=True` makes `name()` raise, the way a process that exited
    between being listed and being inspected would: `process_iter` still knew
    its pid, but nothing about it can be read any more. `name()` rather than
    `cpu_percent()` is the one made to fail, because `cpu_percent()` is also
    called during priming (`_sample`), which has its own, separate
    swallow-and-continue -- raising there would test that path, not the row
    loop's.

    `fails_at_memory_info=True` makes `memory_info()` raise instead, the way
    a process that survives the first three reads but has already exited by
    the time the resident memory is fetched would behave. This targets the
    final read in the row loop's guarded block.
    """

    def __init__(self, pid: int, name: str, cpu_percent: float = 0.0,
                 memory_percent: float = 0.0, rss: int = 0,
                 vanishes: bool = False, fails_at_memory_info: bool = False) -> None:
        self.pid = pid
        self._name = name
        self._cpu_percent = cpu_percent
        self._memory_percent = memory_percent
        self._rss = rss
        self._vanishes = vanishes
        self._fails_at_memory_info = fails_at_memory_info

    def cpu_percent(self, interval: float | None = None) -> float:
        return self._cpu_percent

    def name(self) -> str:
        if self._vanishes:
            raise psutil.NoSuchProcess(self.pid)
        return self._name

    def memory_percent(self) -> float:
        return self._memory_percent

    def memory_info(self):
        if self._fails_at_memory_info:
            raise psutil.NoSuchProcess(self.pid)
        return SimpleNamespace(rss=self._rss)


def test_a_systemd_unit_is_reported_verbatim(tmp_path, monkeypatch):
    _write_cgroup(tmp_path, 1920, "0::/system.slice/glusterd.service\n")
    monkeypatch.setattr(processes, "PROC", str(tmp_path))
    assert processes.cgroup_origin(1920) == "glusterd.service"


def test_a_docker_scope_becomes_a_short_container_id(tmp_path, monkeypatch):
    _write_cgroup(tmp_path, 7372,
                  "0::/system.slice/docker-e23ce43dcbe0feef12bc0199df6bf45d.scope\n")
    monkeypatch.setattr(processes, "PROC", str(tmp_path))
    # Twelve hex characters -- Docker's own short form, so the value can be
    # pasted straight into `docker inspect`.
    assert processes.cgroup_origin(7372) == "container e23ce43dcbe0"


def test_an_unrecognised_cgroup_line_yields_no_origin(tmp_path, monkeypatch):
    _write_cgroup(tmp_path, 42, "0::/user.slice/user-1000.slice/session-3.scope\n")
    monkeypatch.setattr(processes, "PROC", str(tmp_path))
    assert processes.cgroup_origin(42) is None


def test_a_missing_cgroup_file_yields_no_origin(tmp_path, monkeypatch):
    monkeypatch.setattr(processes, "PROC", str(tmp_path))
    assert processes.cgroup_origin(999999) is None


def test_the_panels_own_process_is_never_listed():
    snapshot = processes.collect_processes(sample=0.05)
    assert snapshot is not None
    listed = {p.pid for p in snapshot.top_cpu} | {p.pid for p in snapshot.top_memory}
    assert os.getpid() not in listed


def test_both_lists_are_capped_at_the_limit():
    snapshot = processes.collect_processes(sample=0.05, limit=3)
    assert snapshot is not None
    assert len(snapshot.top_cpu) <= 3
    assert len(snapshot.top_memory) <= 3


def test_the_lists_are_sorted_by_their_own_measure():
    snapshot = processes.collect_processes(sample=0.05)
    assert snapshot is not None
    cpu = [p.cpu_percent for p in snapshot.top_cpu]
    mem = [p.memory_percent for p in snapshot.top_memory]
    assert cpu == sorted(cpu, reverse=True)
    assert mem == sorted(mem, reverse=True)


def test_a_disabled_sample_reports_no_cpu_ranking_rather_than_zeros(tmp_path, monkeypatch):
    """Ranking by a figure nobody measured is the one thing this panel avoids.

    Five rows of 0.0 would read as a measurement; an absent list reads as the
    absence it is. Driven by a fake process table rather than the host's own:
    this used to assert `top_memory` was non-empty against whatever the real
    machine happened to be running, which fails outright in a single-process
    container -- exactly the kind of host this package's CI may well be.
    """
    proc = _FakeProcess(pid=100, name="only-process", memory_percent=3.0)
    monkeypatch.setattr(processes.psutil, "process_iter", lambda: [proc])
    monkeypatch.setattr(processes, "PROC", str(tmp_path))

    snapshot = processes.collect_processes(sample=0.0)

    assert snapshot is not None
    assert snapshot.top_cpu == []
    assert snapshot.sampled == 0.0
    assert snapshot.top_memory == [
        ProcessInfo(pid=100, name="only-process", cpu_percent=None,
                    memory_percent=3.0, memory_bytes=0, origin=None),
    ]


def test_the_sampled_window_is_reported():
    snapshot = processes.collect_processes(sample=0.05)
    assert snapshot is not None
    assert snapshot.sampled == 0.05


def test_a_process_that_vanishes_mid_sample_is_skipped_not_raised(tmp_path, monkeypatch):
    """The row loop's `except (psutil.Error, OSError): continue`, promised by
    the docstring and, until now, never exercised: a process can survive
    priming and still be gone -- or refuse inspection -- by the time its row
    is built, and the rest of the sample must still come back whole.
    """
    survivor = _FakeProcess(pid=100, name="survivor", cpu_percent=5.0, memory_percent=2.0)
    ghost = _FakeProcess(pid=200, name="ghost", vanishes=True)
    monkeypatch.setattr(processes.psutil, "process_iter", lambda: [survivor, ghost])
    monkeypatch.setattr(processes, "PROC", str(tmp_path))
    monkeypatch.setattr(processes.time, "sleep", lambda seconds: None)

    snapshot = processes.collect_processes(sample=0.05)

    assert snapshot is not None
    pids = {p.pid for p in snapshot.top_cpu} | {p.pid for p in snapshot.top_memory}
    assert pids == {100}, "the vanished process must not appear, and the survivor must"


def test_cgroup_origin_is_wired_into_each_row(tmp_path, monkeypatch):
    """Each row's `origin` must come from its own pid's cgroup file -- proof
    the wiring is per-process, not a single value shared across the sample.
    """
    _write_cgroup(tmp_path, 100, "0::/system.slice/myservice.service\n")
    proc = _FakeProcess(pid=100, name="myservice", cpu_percent=1.0, memory_percent=1.0)
    monkeypatch.setattr(processes.psutil, "process_iter", lambda: [proc])
    monkeypatch.setattr(processes, "PROC", str(tmp_path))
    monkeypatch.setattr(processes.time, "sleep", lambda seconds: None)

    snapshot = processes.collect_processes(sample=0.05)

    assert snapshot is not None
    assert snapshot.top_cpu[0].origin == "myservice.service"
    assert snapshot.top_memory[0].origin == "myservice.service"


def test_the_ranking_is_deterministic_with_a_fake_process_table(tmp_path, monkeypatch):
    """Same fixture, exercised end to end: each list orders by its own measure,
    not incidentally by insertion order or by the other list's figure.
    """
    procs = [
        _FakeProcess(pid=1, name="a", cpu_percent=10.0, memory_percent=90.0),
        _FakeProcess(pid=2, name="b", cpu_percent=90.0, memory_percent=10.0),
    ]
    monkeypatch.setattr(processes.psutil, "process_iter", lambda: procs)
    monkeypatch.setattr(processes, "PROC", str(tmp_path))
    monkeypatch.setattr(processes.time, "sleep", lambda seconds: None)

    snapshot = processes.collect_processes(sample=0.05)

    assert snapshot is not None
    assert [p.pid for p in snapshot.top_cpu] == [2, 1]
    assert [p.pid for p in snapshot.top_memory] == [1, 2]


def test_no_process_table_yields_none(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("no process table here")

    monkeypatch.setattr(processes.psutil, "process_iter", boom)
    assert processes.collect_processes(sample=0.05) is None


def test_a_row_carries_the_resident_memory_in_bytes(monkeypatch, tmp_path):
    """The absolute figure and the percentage are one quantity in two units.

    psutil computes `memory_percent()` from `rss` by default, so reading the
    same `rss` here means the two columns can never be seen to disagree.
    """
    monkeypatch.setattr(processes, "PROC", str(tmp_path))
    monkeypatch.setattr(processes.psutil, "process_iter",
                        lambda: [_FakeProcess(101, "app", memory_percent=7.0,
                                              rss=2 * 1024**3)])
    snapshot = processes.collect_processes(sample=0.0)
    assert snapshot is not None
    assert snapshot.top_memory[0].memory_bytes == 2 * 1024**3


def test_a_process_that_raises_while_being_read_is_still_skipped_whole(monkeypatch,
                                                                       tmp_path):
    """One more attribute is now read inside that `try`, so the skip path is
    worth re-checking: a row with a hole in it would be worse than no row."""
    monkeypatch.setattr(processes, "PROC", str(tmp_path))
    monkeypatch.setattr(processes.psutil, "process_iter", lambda: [
        _FakeProcess(101, "app", memory_percent=1.0, rss=1024),
        _FakeProcess(102, "gone", memory_percent=9.0, rss=4096, vanishes=True),
    ])
    snapshot = processes.collect_processes(sample=0.0)
    assert snapshot is not None
    assert [row.pid for row in snapshot.top_memory] == [101]


def test_a_process_that_fails_at_the_memory_read_is_skipped_whole(tmp_path, monkeypatch):
    """The row loop reads four attributes; the last one deserves its own guard.

    The sibling test kills the process at `name()`, the first read, so it
    passes whether or not the memory read is inside the `try` at all. This one
    fails there specifically, which is what pins the read to the guarded block.
    """
    monkeypatch.setattr(processes, "PROC", str(tmp_path))
    monkeypatch.setattr(processes.psutil, "process_iter", lambda: [
        _FakeProcess(101, "app", memory_percent=1.0, rss=1024),
        _FakeProcess(102, "gone", memory_percent=9.0, rss=4096,
                     fails_at_memory_info=True),
    ])
    snapshot = processes.collect_processes(sample=0.0)
    assert snapshot is not None
    assert [row.pid for row in snapshot.top_memory] == [101]


def test_the_limit_is_honoured_above_the_default(monkeypatch, tmp_path):
    monkeypatch.setattr(processes, "PROC", str(tmp_path))
    monkeypatch.setattr(processes.psutil, "process_iter", lambda: [
        _FakeProcess(n, f"p{n}", memory_percent=float(n), rss=n * 1024)
        for n in range(1, 12)
    ])
    snapshot = processes.collect_processes(sample=0.0, limit=8)
    assert snapshot is not None
    assert len(snapshot.top_memory) == 8
