from types import SimpleNamespace

import pytest

from lmu.terminal_status_panel.collectors import resources
from lmu.terminal_status_panel.model import ResourceUsage


@pytest.fixture
def base_mocks(monkeypatch):
    monkeypatch.setattr(
        resources.psutil, "virtual_memory",
        lambda: SimpleNamespace(total=32_000_000_000, used=20_400_000_000, percent=64.0),
    )
    monkeypatch.setattr(
        resources.psutil, "swap_memory",
        lambda: SimpleNamespace(total=8_000_000_000, used=600_000_000, percent=8.0),
    )
    monkeypatch.setattr(resources.psutil, "disk_partitions", lambda all=False: [])
    monkeypatch.setattr(resources.psutil, "getloadavg", lambda: (1.0, 0.7, 0.4))
    monkeypatch.setattr(resources.psutil, "cpu_count", lambda: 4)
    monkeypatch.setattr(
        resources.psutil, "cpu_percent",
        lambda interval=None, percpu=False: [10.0, 20.0, 30.0, 40.0] if percpu else 25.0,
    )


def test_memory_and_swap(base_mocks):
    res = resources.collect_resources()
    assert isinstance(res, ResourceUsage)
    assert res.mem_percent == 64.0
    assert res.swap_used == 600_000_000
    assert res.load_avg == (1.0, 0.7, 0.4)
    assert res.cpu_count == 4


def test_cpu_total_and_per_core(base_mocks):
    res = resources.collect_resources()
    assert res.cpu_per_core == [10.0, 20.0, 30.0, 40.0]
    assert res.cpu_percent == 25.0  # mean of per-core samples


def test_filesystem_filtering(base_mocks, monkeypatch):
    parts = [
        SimpleNamespace(device="/dev/sda1", mountpoint="/", fstype="ext4"),
        SimpleNamespace(device="tmpfs", mountpoint="/run", fstype="tmpfs"),
        SimpleNamespace(device="overlay", mountpoint="/var/lib/docker", fstype="overlay"),
    ]
    monkeypatch.setattr(resources.psutil, "disk_partitions", lambda all=False: parts)
    monkeypatch.setattr(
        resources.psutil, "disk_usage",
        lambda p: SimpleNamespace(total=230_000_000_000, used=210_000_000_000, percent=91.0),
    )
    res = resources.collect_resources()
    assert [fs.mountpoint for fs in res.filesystems] == ["/"]
    assert res.filesystems[0].percent == 91.0


def test_degrades_on_error(monkeypatch):
    def boom():
        raise RuntimeError("no")

    monkeypatch.setattr(resources.psutil, "virtual_memory", boom)
    monkeypatch.setattr(resources.psutil, "swap_memory", boom)
    monkeypatch.setattr(resources.psutil, "disk_partitions", lambda all=False: [])
    monkeypatch.setattr(resources.psutil, "getloadavg", boom)
    monkeypatch.setattr(resources.psutil, "cpu_count", lambda: 4)
    monkeypatch.setattr(resources.psutil, "cpu_percent", boom)
    res = resources.collect_resources()
    assert res.mem_percent is None
    assert res.filesystems == []
    assert res.cpu_per_core == []
    assert res.cpu_percent is None
