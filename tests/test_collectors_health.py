# tests/test_collectors_health.py
from terminal_status_panel.collectors import health as health_collector
from terminal_status_panel.config import Config, HealthConfig
from terminal_status_panel.model import ClusterService, DnsCheck, PeerReachability


def _fqdn() -> str:
    """Keep the tests off the real resolver."""
    return "node.example"


def _config(**kwargs):
    cfg = Config()
    cfg.health = HealthConfig(**kwargs)
    return cfg


def test_collect_health_gathers_all_three_groups(monkeypatch):
    monkeypatch.setattr(
        health_collector, "probe_cluster",
        lambda index, kind, timeout: ClusterService(kind=kind, reachable=True),
    )
    monkeypatch.setattr(
        health_collector, "collect_peers",
        lambda names, timeout: [PeerReachability(name="ccn-01", method="wireguard", ok=True)],
    )
    monkeypatch.setattr(
        health_collector, "collect_dns",
        lambda **kwargs: [DnsCheck(label="Resolver", ok=True, detail="3 ms")],
    )
    health = health_collector.collect_health(
        _config(enabled=["postgres"]), peer_names=["ccn-01"], client=object(),
        resolve_fqdn=_fqdn,
    )
    assert [service.kind for service in health.clusters] == ["postgres"]
    assert health.peers[0].name == "ccn-01"
    assert health.dns[0].label == "Resolver"
    assert health.truncated == []
    assert health.clusters_probed is True


def test_a_check_that_exceeds_the_budget_is_truncated_not_failed(monkeypatch):
    import time

    def slow(*args, **kwargs):
        time.sleep(5)
        return []

    monkeypatch.setattr(health_collector, "probe_cluster", slow)
    monkeypatch.setattr(health_collector, "collect_peers", lambda names, timeout: [])
    monkeypatch.setattr(health_collector, "collect_dns", lambda **kwargs: [])
    health = health_collector.collect_health(
        _config(budget=0.3, enabled=["postgres"]), peer_names=[], client=object(),
        resolve_fqdn=_fqdn,
    )
    assert "postgres" in health.truncated
    assert health.clusters == []
    # Truncation is not the same as never having tried: the task was
    # registered and attempted, it just didn't finish in time.
    assert health.clusters_probed is True


def test_a_raising_check_becomes_an_error_entry_not_a_truncation(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("kaputt")

    monkeypatch.setattr(health_collector, "probe_cluster", boom)
    monkeypatch.setattr(health_collector, "collect_peers", lambda names, timeout: [])
    monkeypatch.setattr(health_collector, "collect_dns", lambda **kwargs: [])
    health = health_collector.collect_health(
        _config(enabled=["postgres"]), peer_names=[], client=object(),
        resolve_fqdn=_fqdn,
    )
    assert health.truncated == []
    assert len(health.clusters) == 1
    assert "kaputt" in health.clusters[0].error


def test_no_docker_client_still_yields_network_and_dns(monkeypatch):
    monkeypatch.setattr(health_collector, "collect_peers", lambda names, timeout: [])
    monkeypatch.setattr(
        health_collector, "collect_dns", lambda **kwargs: [DnsCheck(label="Resolver", ok=True)]
    )
    health = health_collector.collect_health(
        _config(), peer_names=[], client=None, resolve_fqdn=_fqdn
    )
    assert health.clusters == []
    assert health.dns[0].label == "Resolver"
    # No client means the clusters check was never attempted, not that it
    # ran and found nothing.
    assert health.clusters_probed is False


def test_no_enabled_kinds_means_clusters_never_probed(monkeypatch):
    monkeypatch.setattr(health_collector, "collect_peers", lambda names, timeout: [])
    monkeypatch.setattr(health_collector, "collect_dns", lambda **kwargs: [])
    health = health_collector.collect_health(
        _config(enabled=[]), peer_names=[], client=object(), resolve_fqdn=_fqdn
    )
    assert health.clusters == []
    assert health.clusters_probed is False


def test_clusters_probed_and_empty_is_distinct_from_never_probed(monkeypatch):
    """The pair that makes the distinction real: a run whose every kind ran out
    of time must still be marked as probed, unlike the two cases above — the
    renderer then names the kinds instead of claiming nothing was found."""
    import time

    def slow(index, kind, timeout):
        time.sleep(5)
        return ClusterService(kind=kind)

    monkeypatch.setattr(health_collector, "probe_cluster", slow)
    monkeypatch.setattr(health_collector, "collect_peers", lambda names, timeout: [])
    monkeypatch.setattr(health_collector, "collect_dns", lambda **kwargs: [])
    health = health_collector.collect_health(
        _config(budget=0.3, enabled=["postgres", "kafka"]), peer_names=[],
        client=object(), resolve_fqdn=_fqdn,
    )
    assert health.clusters == []
    assert health.clusters_probed is True
    assert sorted(health.truncated) == ["kafka", "postgres"]


def test_a_hung_kind_loses_only_its_own_result(monkeypatch):
    """The point of one budget task per kind: a hung RustFS must not hide a
    PostgreSQL quorum loss that was measured two seconds earlier."""
    import time

    def probe(index, kind, timeout):
        if kind == "rustfs":
            time.sleep(5)
        return ClusterService(kind=kind, quorum_ok=False)

    monkeypatch.setattr(health_collector, "probe_cluster", probe)
    monkeypatch.setattr(health_collector, "collect_peers", lambda names, timeout: [])
    monkeypatch.setattr(health_collector, "collect_dns", lambda **kwargs: [])
    health = health_collector.collect_health(
        _config(budget=0.5, enabled=["postgres", "rustfs"]), peer_names=[],
        client=object(), resolve_fqdn=_fqdn,
    )
    assert [service.kind for service in health.clusters] == ["postgres"]
    assert health.truncated == ["rustfs"]


def test_each_kind_is_probed_with_its_own_configured_timeout(monkeypatch):
    """health.timeout.* was parsed, documented and rolled out — and never
    applied to a single cluster probe."""
    seen = {}

    def probe(index, kind, timeout):
        seen[kind] = timeout
        return ClusterService(kind=kind)

    monkeypatch.setattr(health_collector, "probe_cluster", probe)
    monkeypatch.setattr(health_collector, "collect_peers", lambda names, timeout: [])
    monkeypatch.setattr(health_collector, "collect_dns", lambda **kwargs: [])
    health_collector.collect_health(
        _config(
            enabled=["postgres", "kafka"],
            timeouts={"postgres": 0.9, "kafka": 4.0, "dns": 2.5, "wireguard": 1.0},
        ),
        peer_names=[], client=object(), resolve_fqdn=_fqdn,
    )
    assert seen == {"postgres": 0.9, "kafka": 4.0}


def test_all_kinds_share_one_container_index(monkeypatch):
    """One Docker container listing per run, not one per kind."""
    seen = []

    def probe(index, kind, timeout):
        seen.append(index)
        return ClusterService(kind=kind)

    monkeypatch.setattr(health_collector, "probe_cluster", probe)
    monkeypatch.setattr(health_collector, "collect_peers", lambda names, timeout: [])
    monkeypatch.setattr(health_collector, "collect_dns", lambda **kwargs: [])
    health_collector.collect_health(
        _config(enabled=["postgres", "kafka", "rustfs"]), peer_names=[],
        client=object(), resolve_fqdn=_fqdn,
    )
    assert len(seen) == 3
    assert all(index is seen[0] for index in seen)


def test_the_container_index_is_built_inside_the_budget(monkeypatch):
    """It talks to the Docker socket, so it must not be built on the main
    thread ahead of the budget — that is the mistake socket.getfqdn() made."""
    import threading

    main_thread = threading.current_thread()
    seen = []

    class _Client:
        @property
        def containers(self):
            seen.append(threading.current_thread())
            raise RuntimeError("no daemon")

    # The real probe_cluster runs here, so the index is exercised the way
    # production exercises it.
    monkeypatch.setattr(health_collector, "collect_peers", lambda names, timeout: [])
    monkeypatch.setattr(health_collector, "collect_dns", lambda **kwargs: [])
    health_collector.collect_health(
        _config(enabled=["postgres"]), peer_names=[], client=_Client(),
        resolve_fqdn=_fqdn,
    )
    assert seen and all(thread is not main_thread for thread in seen)


def test_the_peer_list_is_fetched_once_and_inside_the_budget(monkeypatch):
    """It comes from the Docker daemon, so it belongs on a check thread — and
    both checks that need it must share the one fetch."""
    import threading

    main_thread = threading.current_thread()
    callers = []

    def resolve():
        callers.append(threading.current_thread())
        return ["ccn-01"]

    seen = {}
    monkeypatch.setattr(
        health_collector, "collect_peers",
        lambda names, timeout: seen.setdefault("peers", names) and [],
    )
    monkeypatch.setattr(
        health_collector, "collect_dns",
        lambda **kwargs: seen.setdefault("dns", kwargs["peer_names"]) and [],
    )
    health = health_collector.collect_health(
        _config(enabled=[]), peer_names=[], client=None, resolve_fqdn=_fqdn,
        resolve_peer_names=resolve,
    )
    assert len(callers) == 1
    assert callers[0] is not main_thread
    assert seen["peers"] == ["ccn-01"]
    assert seen["dns"] == ["ccn-01"]
    # A resolved peer list is something to ask about, so the block counts as
    # probed even though no peer answered.
    assert health.peers_probed is True


def test_a_known_peer_list_is_not_fetched_again(monkeypatch):
    """The Docker section already collected the node names; asking the daemon a
    second time would be a round trip for nothing."""
    monkeypatch.setattr(health_collector, "collect_peers", lambda names, timeout: [])
    monkeypatch.setattr(health_collector, "collect_dns", lambda **kwargs: [])

    def resolve():
        raise AssertionError("must not be called when the names are known")

    health_collector.collect_health(
        _config(enabled=[]), peer_names=["ccn-01"], client=None, resolve_fqdn=_fqdn,
        resolve_peer_names=resolve,
    )


def test_collect_health_passes_the_configured_dns_expectations(monkeypatch):
    from terminal_status_panel.config import DnsExpectation

    captured = {}

    monkeypatch.setattr(
        health_collector, "probe_cluster",
        lambda index, kind, timeout: ClusterService(kind=kind),
    )
    monkeypatch.setattr(health_collector, "collect_peers", lambda names, timeout: [])

    def capture(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(health_collector, "collect_dns", capture)
    health_collector.collect_health(
        _config(dns_expect=[DnsExpectation(name="login.example.net", addresses=["10.9.9.9"])]),
        peer_names=["ccn-01"], client=object(), resolve_fqdn=_fqdn,
    )
    assert captured["expectations"] == [("login.example.net", ["10.9.9.9"])]
    assert captured["peer_names"] == ["ccn-01"]


def test_peers_probed_is_false_without_names_or_answers(monkeypatch):
    monkeypatch.setattr(
        health_collector, "probe_cluster",
        lambda index, kind, timeout: ClusterService(kind=kind),
    )
    monkeypatch.setattr(health_collector, "collect_peers", lambda names, timeout: [])
    monkeypatch.setattr(health_collector, "collect_dns", lambda **kwargs: [])
    health = health_collector.collect_health(
        _config(), peer_names=[], client=object(), resolve_fqdn=_fqdn
    )
    assert health.peers == []
    assert health.peers_probed is False


def test_a_slow_fqdn_lookup_is_truncated_rather_than_delaying_the_login(monkeypatch):
    """The own-name lookup runs inside the budget, so a broken resolver costs
    the DNS check its result — not the login shell its prompt."""
    import time

    monkeypatch.setattr(
        health_collector, "probe_cluster",
        lambda index, kind, timeout: ClusterService(kind=kind),
    )
    monkeypatch.setattr(health_collector, "collect_peers", lambda names, timeout: [])
    monkeypatch.setattr(health_collector, "collect_dns", lambda **kwargs: [])

    def slow_fqdn():
        time.sleep(5)
        return "node.example"

    started = time.monotonic()
    health = health_collector.collect_health(
        _config(budget=0.3), peer_names=[], client=object(), resolve_fqdn=slow_fqdn
    )
    assert time.monotonic() - started < 1.5
    assert "dns" in health.truncated


def test_peers_probed_is_true_when_names_were_available(monkeypatch):
    monkeypatch.setattr(
        health_collector, "probe_cluster",
        lambda index, kind, timeout: ClusterService(kind=kind),
    )
    monkeypatch.setattr(health_collector, "collect_peers", lambda names, timeout: [])
    monkeypatch.setattr(health_collector, "collect_dns", lambda **kwargs: [])
    health = health_collector.collect_health(
        _config(), peer_names=["ccn-01"], client=object(), resolve_fqdn=_fqdn
    )
    assert health.peers == []
    assert health.peers_probed is True
