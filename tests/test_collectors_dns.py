from terminal_status_panel.collectors import dns as dns_collector


class _StubResolver:
    """Minimal stand-in for dns.resolver.Resolver."""

    def __init__(self, answers=None, reverse=None, nameservers=("127.0.0.53",)):
        self._answers = answers or {}
        self._reverse = reverse or {}
        self.nameservers = list(nameservers)
        self.lifetime = 1.0

    def resolve(self, name, rdtype="A", **kwargs):
        key = (str(name).rstrip("."), rdtype)
        if key not in self._answers:
            raise LookupError(f"no answer for {key}")
        return self._answers[key]

    def resolve_address(self, address, **kwargs):
        if address not in self._reverse:
            raise LookupError(f"no PTR for {address}")
        return self._reverse[address]


def _hosts(tmp_path, content):
    path = tmp_path / "hosts"
    path.write_text(content)
    return str(path)


def test_read_hosts_file_maps_every_name_to_its_addresses(tmp_path):
    path = _hosts(tmp_path, "127.0.0.1 localhost\n10.0.0.1 node1.example node1  # comment\n")
    mapping = dns_collector.read_hosts_file(path)
    assert mapping["localhost"] == {"127.0.0.1"}
    assert mapping["node1.example"] == {"10.0.0.1"}
    assert mapping["node1"] == {"10.0.0.1"}


def test_read_hosts_file_ignores_comments_and_blank_lines(tmp_path):
    path = _hosts(tmp_path, "\n# only a comment\n   \n")
    assert dns_collector.read_hosts_file(path) == {}


def test_read_hosts_file_survives_a_missing_file():
    assert dns_collector.read_hosts_file("/nonexistent/hosts") == {}


def test_resolver_check_reports_latency(tmp_path):
    resolver = _StubResolver(answers={("node1.example", "A"): ["10.0.0.1"]})
    checks = dns_collector.collect_dns(
        fqdn="node1.example",
        peer_names=[],
        expectations=[],
        timeout=1.0,
        resolver=resolver,
        hosts_path=_hosts(tmp_path, ""),
    )
    resolver_check = [c for c in checks if c.label.startswith("Resolver")][0]
    assert resolver_check.ok is True
    assert "ms" in resolver_check.detail


def test_forward_and_reverse_are_consistent(tmp_path):
    resolver = _StubResolver(
        answers={("node1.example", "A"): ["10.0.0.1"]},
        reverse={"10.0.0.1": ["node1.example."]},
    )
    checks = dns_collector.collect_dns(
        fqdn="node1.example",
        peer_names=[],
        expectations=[],
        timeout=1.0,
        resolver=resolver,
        hosts_path=_hosts(tmp_path, ""),
    )
    own = [c for c in checks if c.label == "own FQDN"][0]
    assert own.ok is True


def test_reverse_pointing_elsewhere_is_a_failure(tmp_path):
    resolver = _StubResolver(
        answers={("node1.example", "A"): ["10.0.0.1"]},
        reverse={"10.0.0.1": ["somebodyelse.example."]},
    )
    checks = dns_collector.collect_dns(
        fqdn="node1.example",
        peer_names=[],
        expectations=[],
        timeout=1.0,
        resolver=resolver,
        hosts_path=_hosts(tmp_path, ""),
    )
    own = [c for c in checks if c.label == "own FQDN"][0]
    assert own.ok is False


def test_all_peers_resolving_is_one_summary_check(tmp_path):
    resolver = _StubResolver(
        answers={
            ("node1.example", "A"): ["10.0.0.1"],
            ("node2.example", "A"): ["10.0.0.2"],
        }
    )
    checks = dns_collector.collect_dns(
        fqdn="node1.example",
        peer_names=["node1.example", "node2.example"],
        expectations=[],
        timeout=1.0,
        resolver=resolver,
        hosts_path=_hosts(tmp_path, ""),
    )
    peers = [c for c in checks if c.label == "Peers"][0]
    assert peers.ok is True
    assert "2/2" in peers.detail


def test_a_peer_that_does_not_resolve_fails_the_summary(tmp_path):
    resolver = _StubResolver(answers={("node1.example", "A"): ["10.0.0.1"]})
    checks = dns_collector.collect_dns(
        fqdn="node1.example",
        peer_names=["node1.example", "ghost.example"],
        expectations=[],
        timeout=1.0,
        resolver=resolver,
        hosts_path=_hosts(tmp_path, ""),
    )
    peers = [c for c in checks if c.label == "Peers"][0]
    assert peers.ok is False
    assert "ghost.example" in peers.detail


def test_expectation_with_matching_address_passes(tmp_path):
    resolver = _StubResolver(answers={("login.example.net", "A"): ["10.9.9.9"]})
    checks = dns_collector.collect_dns(
        fqdn="node1.example",
        peer_names=[],
        expectations=[("login.example.net", ["10.9.9.9"])],
        timeout=1.0,
        resolver=resolver,
        hosts_path=_hosts(tmp_path, ""),
    )
    check = [c for c in checks if c.label == "login.example.net"][0]
    assert check.ok is True


def test_expectation_with_wrong_address_fails(tmp_path):
    resolver = _StubResolver(answers={("login.example.net", "A"): ["10.9.9.9"]})
    checks = dns_collector.collect_dns(
        fqdn="node1.example",
        peer_names=[],
        expectations=[("login.example.net", ["10.1.1.1"])],
        timeout=1.0,
        resolver=resolver,
        hosts_path=_hosts(tmp_path, ""),
    )
    check = [c for c in checks if c.label == "login.example.net"][0]
    assert check.ok is False
    assert "10.9.9.9" in check.detail


def test_hosts_file_diverging_from_dns_is_a_warning_not_a_failure(tmp_path):
    resolver = _StubResolver(answers={("node1.example", "A"): ["10.0.0.1"]})
    checks = dns_collector.collect_dns(
        fqdn="node1.example",
        peer_names=["node1.example"],
        expectations=[],
        timeout=1.0,
        resolver=resolver,
        hosts_path=_hosts(tmp_path, "10.0.0.99 node1.example\n"),
    )
    hosts_check = [c for c in checks if c.label == "/etc/hosts"][0]
    assert hosts_check.ok is None, "divergence is deliberate often enough to be a warning"
    assert "node1.example" in hosts_check.detail


def test_hosts_file_agreeing_with_dns_passes(tmp_path):
    resolver = _StubResolver(answers={("node1.example", "A"): ["10.0.0.1"]})
    checks = dns_collector.collect_dns(
        fqdn="node1.example",
        peer_names=["node1.example"],
        expectations=[],
        timeout=1.0,
        resolver=resolver,
        hosts_path=_hosts(tmp_path, "10.0.0.1 node1.example\n"),
    )
    hosts_check = [c for c in checks if c.label == "/etc/hosts"][0]
    assert hosts_check.ok is True


def test_collect_dns_never_raises_when_the_resolver_explodes(tmp_path):
    class _Broken:
        nameservers = ["127.0.0.53"]
        lifetime = 1.0

        def resolve(self, *a, **k):
            raise RuntimeError("resolver on fire")

        def resolve_address(self, *a, **k):
            raise RuntimeError("resolver on fire")

    checks = dns_collector.collect_dns(
        fqdn="node1.example",
        peer_names=["node1.example"],
        expectations=[],
        timeout=1.0,
        resolver=_Broken(),
        hosts_path=_hosts(tmp_path, ""),
    )
    assert all(check.ok is not True for check in checks)
