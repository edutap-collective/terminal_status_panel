import os

from terminal_status_panel.collectors import processes


def _write_cgroup(tmp_path, pid: int, line: str) -> None:
    target = tmp_path / str(pid)
    target.mkdir()
    (target / "cgroup").write_text(line)


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


def test_a_disabled_sample_reports_no_cpu_ranking_rather_than_zeros():
    """Ranking by a figure nobody measured is the one thing this panel avoids.

    Five rows of 0.0 would read as a measurement; an absent list reads as the
    absence it is.
    """
    snapshot = processes.collect_processes(sample=0.0)
    assert snapshot is not None
    assert snapshot.top_cpu == []
    assert snapshot.sampled == 0.0
    assert all(p.cpu_percent is None for p in snapshot.top_memory)
    assert snapshot.top_memory, "memory is still measurable without a window"


def test_the_sampled_window_is_reported():
    snapshot = processes.collect_processes(sample=0.05)
    assert snapshot is not None
    assert snapshot.sampled == 0.05


def test_no_process_table_yields_none(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("no process table here")

    monkeypatch.setattr(processes.psutil, "process_iter", boom)
    assert processes.collect_processes(sample=0.05) is None
