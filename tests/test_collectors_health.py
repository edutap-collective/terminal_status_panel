# tests/test_collectors_health.py
from terminal_status_panel.collectors import health as health_collector
from terminal_status_panel.config import Config, HealthConfig
from terminal_status_panel.model import ClusterService, DnsCheck, PeerReachability


def _config(**kwargs):
    cfg = Config()
    cfg.health = HealthConfig(**kwargs)
    return cfg


def test_collect_health_gathers_all_three_groups(monkeypatch):
    monkeypatch.setattr(
        health_collector, "collect_clusters",
        lambda client, kinds: [ClusterService(kind="postgres", reachable=True)],
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
        _config(), fqdn="node.example", peer_names=["ccn-01"], client=object()
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

    monkeypatch.setattr(health_collector, "collect_clusters", slow)
    monkeypatch.setattr(health_collector, "collect_peers", lambda names, timeout: [])
    monkeypatch.setattr(health_collector, "collect_dns", lambda **kwargs: [])
    health = health_collector.collect_health(
        _config(budget=0.3), fqdn="node.example", peer_names=[], client=object()
    )
    assert "clusters" in health.truncated
    assert health.clusters == []
    # Truncation is not the same as never having tried: the task was
    # registered and attempted, it just didn't finish in time.
    assert health.clusters_probed is True


def test_a_raising_check_becomes_an_error_entry_not_a_truncation(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("kaputt")

    monkeypatch.setattr(health_collector, "collect_clusters", boom)
    monkeypatch.setattr(health_collector, "collect_peers", lambda names, timeout: [])
    monkeypatch.setattr(health_collector, "collect_dns", lambda **kwargs: [])
    health = health_collector.collect_health(
        _config(), fqdn="node.example", peer_names=[], client=object()
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
        _config(), fqdn="node.example", peer_names=[], client=None
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
        _config(enabled=[]), fqdn="node.example", peer_names=[], client=object()
    )
    assert health.clusters == []
    assert health.clusters_probed is False


def test_clusters_probed_and_empty_is_distinct_from_never_probed(monkeypatch):
    """The pair that makes the distinction real: a real run that legitimately
    finds nothing must still be marked as probed, unlike the two cases above.
    """
    monkeypatch.setattr(health_collector, "collect_clusters", lambda client, kinds: [])
    monkeypatch.setattr(health_collector, "collect_peers", lambda names, timeout: [])
    monkeypatch.setattr(health_collector, "collect_dns", lambda **kwargs: [])
    health = health_collector.collect_health(
        _config(), fqdn="node.example", peer_names=[], client=object()
    )
    assert health.clusters == []
    assert health.clusters_probed is True


def test_collect_health_passes_the_configured_dns_expectations(monkeypatch):
    from terminal_status_panel.config import DnsExpectation

    captured = {}

    monkeypatch.setattr(health_collector, "collect_clusters", lambda client, kinds: [])
    monkeypatch.setattr(health_collector, "collect_peers", lambda names, timeout: [])

    def capture(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(health_collector, "collect_dns", capture)
    health_collector.collect_health(
        _config(dns_expect=[DnsExpectation(name="login.lmu.de", addresses=["10.9.9.9"])]),
        fqdn="node.example", peer_names=["ccn-01"], client=object(),
    )
    assert captured["expectations"] == [("login.lmu.de", ["10.9.9.9"])]
    assert captured["peer_names"] == ["ccn-01"]


def test_peers_probed_is_false_without_names_or_answers(monkeypatch):
    monkeypatch.setattr(health_collector, "collect_clusters", lambda client, kinds: [])
    monkeypatch.setattr(health_collector, "collect_peers", lambda names, timeout: [])
    monkeypatch.setattr(health_collector, "collect_dns", lambda **kwargs: [])
    health = health_collector.collect_health(
        _config(), fqdn="node.example", peer_names=[], client=object()
    )
    assert health.peers == []
    assert health.peers_probed is False


def test_peers_probed_is_true_when_names_were_available(monkeypatch):
    monkeypatch.setattr(health_collector, "collect_clusters", lambda client, kinds: [])
    monkeypatch.setattr(health_collector, "collect_peers", lambda names, timeout: [])
    monkeypatch.setattr(health_collector, "collect_dns", lambda **kwargs: [])
    health = health_collector.collect_health(
        _config(), fqdn="node.example", peer_names=["ccn-01"], client=object()
    )
    assert health.peers == []
    assert health.peers_probed is True
