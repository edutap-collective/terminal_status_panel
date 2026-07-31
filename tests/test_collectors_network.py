# tests/test_collectors_network.py
from terminal_status_panel.collectors import network

# interface line: 5 fields. peer lines: 9 fields.
WG_DUMP = "\t".join(["wg0", "privkey", "pubkey", "51820", "off"]) + "\n" + "\n".join(
    "\t".join(row)
    for row in [
        ["wg0", "peerA", "(none)", "10.1.0.1:51820", "10.9.0.1/32", "1000", "1", "2", "25"],
        ["wg0", "peerB", "(none)", "10.1.0.2:51820", "10.9.0.2/32", "700", "1", "2", "25"],
        ["wg0", "peerC", "(none)", "(none)", "10.9.0.3/32", "0", "0", "0", "off"],
    ]
)

HOSTS = {"wg-node-a": {"10.9.0.1"}, "wg-node-b": {"10.9.0.2"}}


def test_parse_wg_dump_skips_the_interface_line():
    peers = network.parse_wg_dump(WG_DUMP, now=1000.0, hosts=HOSTS)
    assert len(peers) == 3
    assert all(peer.method == "wireguard" for peer in peers)


def test_parse_wg_dump_names_peers_from_the_hosts_file():
    peers = network.parse_wg_dump(WG_DUMP, now=1000.0, hosts=HOSTS)
    assert peers[0].name == "wg-node-a"
    assert peers[1].name == "wg-node-b"


def test_parse_wg_dump_falls_back_to_the_tunnel_ip_when_unknown():
    peers = network.parse_wg_dump(WG_DUMP, now=1000.0, hosts=HOSTS)
    assert peers[2].name == "10.9.0.3"


def test_recent_handshake_is_healthy():
    peers = network.parse_wg_dump(WG_DUMP, now=1000.0, hosts=HOSTS)
    assert peers[0].ok is True
    assert peers[0].detail == "0:00"


def test_handshake_older_than_three_minutes_is_not_ok():
    peers = network.parse_wg_dump(WG_DUMP, now=1000.0, hosts=HOSTS)
    assert peers[1].ok is False  # 300s old
    assert peers[1].detail == "5:00"


def test_a_peer_that_never_handshook_is_not_ok():
    peers = network.parse_wg_dump(WG_DUMP, now=1000.0, hosts=HOSTS)
    assert peers[2].ok is False
    assert peers[2].detail == "never"


def test_collect_peers_falls_back_to_tcp_when_sudo_is_refused(monkeypatch):
    def refuse(*args, **kwargs):
        raise OSError("sudo: a password is required")

    monkeypatch.setattr(network.subprocess, "run", refuse)

    opened = []

    def fake_connection(address, timeout=None):
        opened.append(address)
        if address[0] == "down.example":
            raise OSError("refused")

        class _Socket:
            def close(self):
                pass

        return _Socket()

    monkeypatch.setattr(network.socket, "create_connection", fake_connection)

    peers = network.collect_peers(["up.example", "down.example"], timeout=1.0)
    assert [peer.method for peer in peers] == ["tcp", "tcp"]
    assert peers[0].ok is True
    assert peers[1].ok is False
    assert opened == [("up.example", 2377), ("down.example", 2377)]


def test_collect_peers_prefers_wireguard_when_sudo_works(monkeypatch):
    class _Completed:
        returncode = 0
        stdout = WG_DUMP

    monkeypatch.setattr(network.subprocess, "run", lambda *a, **k: _Completed())
    monkeypatch.setattr(network, "read_hosts_file", lambda *a, **k: HOSTS)
    peers = network.collect_peers(["ignored.example"], timeout=1.0)
    assert [peer.method for peer in peers] == ["wireguard"] * 3


def test_collect_peers_never_raises(monkeypatch):
    monkeypatch.setattr(network.subprocess, "run", lambda *a, **k: 1 / 0)
    monkeypatch.setattr(network.socket, "create_connection", lambda *a, **k: 1 / 0)
    assert network.collect_peers(["x.example"], timeout=1.0)[0].ok is False
