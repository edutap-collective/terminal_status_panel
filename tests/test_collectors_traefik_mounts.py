"""Where a path inside the Traefik container comes from, and whether it can be read here."""

import os

import pytest

from terminal_status_panel.collectors import traefik_mounts as mounts
from terminal_status_panel.collectors.traefik_static import FileProvider

HERE = mounts.KnownPlacement(here=True)
ELSEWHERE = mounts.KnownPlacement(here=False, node="swarm01-wrk-02")


class _Service:
    def __init__(self, container_spec):
        self.name = "demo_traefik"
        self.attrs = {"Spec": {"TaskTemplate": {"ContainerSpec": container_spec}}}


class _Container:
    def __init__(self, attrs):
        self.name = "demo-traefik-1"
        self.attrs = attrs


def _workload(*items):
    return mounts.Workload(name="t", args=[], env={}, workdir=None, mounts=list(items))


def test_a_service_spec_is_normalised():
    service = _Service(
        {
            "Args": ["--configFile=/etc/traefik/traefik.yaml"],
            "Env": ["HOME=/root", "BROKEN"],
            "Dir": "/work",
            "Mounts": [
                {"Type": "bind", "Source": "/srv/t.yaml", "Target": "/etc/traefik/traefik.yaml"}
            ],
            "Configs": [
                {
                    "ConfigName": "traefik_dynamic_yml_v3",
                    "File": {"Name": "/dynamic/00-cluster.yml"},
                },
                {"ConfigName": "plain"},
            ],
        }
    )

    workload = mounts.workload_from_service(service)

    assert workload.args == ["--configFile=/etc/traefik/traefik.yaml"]
    assert workload.env == {"HOME": "/root"}
    assert workload.workdir == "/work"
    assert workload.service is service
    assert mounts.Mount("bind", "/etc/traefik/traefik.yaml", "/srv/t.yaml") in workload.mounts
    assert (
        mounts.Mount("config", "/dynamic/00-cluster.yml", "traefik_dynamic_yml_v3")
        in workload.mounts
    )
    # A config without a file name lands at /<config name>, as the daemon places it.
    assert mounts.Mount("config", "/plain", "plain") in workload.mounts


def test_a_container_inspect_is_normalised():
    container = _Container(
        {
            "Args": ["--configFile=/etc/traefik/traefik.yaml"],
            "Config": {"Env": ["A=b"], "WorkingDir": ""},
            "Mounts": [
                {
                    "Type": "bind",
                    "Source": "/srv/t.yaml",
                    "Destination": "/etc/traefik/traefik.yaml",
                },
                {
                    "Type": "volume",
                    "Name": "acme",
                    "Source": "/var/lib/docker/volumes/acme/_data",
                    "Destination": "/acme",
                },
            ],
        }
    )

    workload = mounts.workload_from_container(container)

    assert workload.service is None
    assert workload.workdir is None
    assert mounts.Mount("volume", "/acme", "acme") in workload.mounts


def test_a_config_matches_only_its_exact_target():
    workload = _workload(mounts.Mount("config", "/dynamic/a.yml", "cfg_a"))

    assert mounts.locate(workload, "/dynamic/a.yml").mount.source == "cfg_a"
    assert mounts.locate(workload, "/dynamic/a.yml.bak").mount is None


def test_a_bind_mounted_directory_covers_the_paths_below_it():
    workload = _workload(mounts.Mount("bind", "/etc/traefik", "/srv/traefik"))

    located = mounts.locate(workload, "/etc/traefik/dynamic/x.yml")

    assert located.host_path == "/srv/traefik/dynamic/x.yml"


def test_the_longest_target_wins_as_the_kernel_resolves_it():
    workload = _workload(
        mounts.Mount("bind", "/etc/traefik", "/srv/traefik"),
        mounts.Mount("config", "/etc/traefik/traefik.yml", "static_v2"),
    )

    assert mounts.locate(workload, "/etc/traefik/traefik.yml").mount.kind == "config"


def test_a_prefix_that_is_not_a_path_component_does_not_match():
    workload = _workload(mounts.Mount("bind", "/etc/traefik", "/srv/traefik"))

    assert mounts.locate(workload, "/etc/traefik2/x.yml").mount is None


def test_a_config_is_read_from_the_api_on_any_node():
    located = mounts.Located("/dynamic/a.yml", mounts.Mount("config", "/dynamic/a.yml", "cfg_a"))

    text = mounts.read_located(located, configs={"cfg_a": "http: {}"}, placement=ELSEWHERE)

    assert text == "http: {}"


def test_an_undecodable_config_says_so():
    located = mounts.Located("/d/a.yml", mounts.Mount("config", "/d/a.yml", "cfg_a"))

    with pytest.raises(mounts.Unreadable, match="cfg_a: config data is not decodable"):
        mounts.read_located(located, configs={"cfg_a": None}, placement=HERE)


def test_a_bind_mount_is_read_when_the_task_runs_here(tmp_path):
    source = tmp_path / "traefik.yaml"
    source.write_text("entryPoints: {}\n")
    located = mounts.locate(
        _workload(mounts.Mount("bind", "/etc/traefik/traefik.yaml", str(source))),
        "/etc/traefik/traefik.yaml",
    )

    assert mounts.read_located(located, configs={}, placement=HERE) == "entryPoints: {}\n"


def test_a_bind_mount_on_another_node_is_not_read_and_the_node_is_named(tmp_path):
    source = tmp_path / "traefik.yaml"
    source.write_text("entryPoints: {}\n")
    located = mounts.locate(
        _workload(mounts.Mount("bind", "/etc/traefik/traefik.yaml", str(source))),
        "/etc/traefik/traefik.yaml",
    )

    with pytest.raises(mounts.Unreadable) as caught:
        mounts.read_located(located, configs={}, placement=ELSEWHERE)

    assert "not readable on this node" in str(caught.value)
    assert "swarm01-wrk-02" in str(caught.value)


@pytest.mark.parametrize(
    "mount,expected",
    [
        (mounts.Mount("volume", "/etc/traefik", "traefik_conf"), "on volume traefik_conf"),
        (mounts.Mount("tmpfs", "/etc/traefik", ""), "on a tmpfs mount"),
    ],
)
def test_volumes_and_tmpfs_are_never_read(mount, expected):
    located = mounts.locate(_workload(mount), "/etc/traefik/traefik.yml")

    with pytest.raises(mounts.Unreadable, match=expected):
        mounts.read_located(located, configs={}, placement=HERE)


def test_a_file_over_the_size_limit_is_not_read(tmp_path):
    source = tmp_path / "big.yml"
    source.write_bytes(b"#" * (mounts.MAX_FILE_BYTES + 1))
    located = mounts.locate(
        _workload(mounts.Mount("bind", "/d/big.yml", str(source))), "/d/big.yml"
    )

    with pytest.raises(mounts.Unreadable, match="larger than 1 MiB"):
        mounts.read_located(located, configs={}, placement=HERE)


def test_a_symlink_leading_out_of_the_bind_source_is_not_followed(tmp_path):
    outside = tmp_path / "secret.yml"
    outside.write_text("x: 1\n")
    root = tmp_path / "dynamic"
    root.mkdir()
    (root / "link.yml").symlink_to(outside)
    located = mounts.locate(_workload(mounts.Mount("bind", "/d", str(root))), "/d/link.yml")

    with pytest.raises(mounts.Unreadable, match="leads outside"):
        mounts.read_located(located, configs={}, placement=HERE)


def test_provider_directory_collects_configs_and_bind_files_by_path(tmp_path):
    (tmp_path / "dynamic.yaml").write_text("http: {}\n")
    workload = _workload(
        mounts.Mount("config", "/dynamic/00-cluster.yml", "traefik_dynamic_yml_v3"),
        mounts.Mount("config", "/certs/rootca.pem", "traefik_rootca_v1"),
        mounts.Mount("bind", "/dynamic/extra.yaml", str(tmp_path / "dynamic.yaml")),
    )

    listing = mounts.provider_files(workload, FileProvider(directory="/dynamic/"), placement=HERE)

    assert [located.path for located in listing.files] == [
        "/dynamic/00-cluster.yml",
        "/dynamic/extra.yaml",
    ]


def test_provider_directory_walks_a_bind_mounted_directory_recursively(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.yml").write_text("")
    (tmp_path / "sub" / "b.TOML").write_text("")
    (tmp_path / ".hidden.yaml").write_text("")
    (tmp_path / "notes.txt").write_text("")
    workload = _workload(mounts.Mount("bind", "/dyn", str(tmp_path)))

    listing = mounts.provider_files(workload, FileProvider(directory="/dyn"), placement=HERE)

    assert [located.path for located in listing.files] == [
        "/dyn/.hidden.yaml",
        "/dyn/a.yml",
        "/dyn/sub/b.TOML",
    ]


def test_provider_directory_on_another_node_is_a_note_not_a_listing(tmp_path):
    workload = _workload(mounts.Mount("bind", "/dyn", str(tmp_path)))

    listing = mounts.provider_files(workload, FileProvider(directory="/dyn"), placement=ELSEWHERE)

    assert listing.files == []
    assert "not readable on this node" in listing.notes[0]


def test_provider_directory_stops_at_the_file_limit(tmp_path):
    for number in range(mounts.MAX_PROVIDER_FILES + 3):
        (tmp_path / f"{number:03}.yml").write_text("")
    workload = _workload(mounts.Mount("bind", "/dyn", str(tmp_path)))

    listing = mounts.provider_files(workload, FileProvider(directory="/dyn"), placement=HERE)

    assert len(listing.files) == mounts.MAX_PROVIDER_FILES
    assert "more than 64 files" in listing.notes[0]


def test_provider_filename_is_the_one_path():
    workload = _workload(mounts.Mount("config", "/dyn.toml", "cfg"))

    listing = mounts.provider_files(workload, FileProvider(filename="/dyn.toml"), placement=HERE)

    assert [located.path for located in listing.files] == ["/dyn.toml"]


@pytest.mark.parametrize(
    "path,fmt",
    [
        ("/a.yml", "yaml"),
        ("/a.YAML", "yaml"),
        ("/a.toml", "toml"),
        ("/traefik_dynamic_yml_v3", None),
    ],
)
def test_the_format_follows_the_extension(path, fmt):
    assert mounts.format_of(path) == fmt


# --- Fix round 1 -------------------------------------------------------


def test_a_nested_bind_does_not_leak_files_from_outside_the_provider_directory(tmp_path):
    outer = tmp_path / "outer"
    outer.mkdir()
    (outer / "acme.yaml").write_text("")
    (outer / "traefik.yml").write_text("")
    inner = tmp_path / "inner"
    inner.mkdir()
    (inner / "x.yml").write_text("")
    workload = _workload(
        mounts.Mount("bind", "/etc/traefik", str(outer)),
        mounts.Mount("bind", "/etc/traefik/dynamic", str(inner)),
    )

    listing = mounts.provider_files(
        workload, FileProvider(directory="/etc/traefik/dynamic"), placement=HERE
    )

    paths = [located.path for located in listing.files]
    assert paths == ["/etc/traefik/dynamic/x.yml"]
    assert all(p == "/etc/traefik/dynamic" or p.startswith("/etc/traefik/dynamic/") for p in paths)


def test_a_nested_bind_directory_on_another_node_gives_a_note():
    workload = _workload(
        mounts.Mount("config", "/dyn/a.yml", "cfg_a"),
        mounts.Mount("bind", "/dyn/extra", "/nonexistent/extra"),
    )

    listing = mounts.provider_files(workload, FileProvider(directory="/dyn"), placement=ELSEWHERE)

    assert [located.path for located in listing.files] == ["/dyn/a.yml"]
    assert any("not readable on this node" in note for note in listing.notes)


def test_a_missing_bind_source_gives_a_note_not_silence(tmp_path):
    workload = _workload(mounts.Mount("bind", "/dyn", str(tmp_path / "missing")))

    listing = mounts.provider_files(workload, FileProvider(directory="/dyn"), placement=HERE)

    assert listing.files == []
    assert "not listed" in listing.notes[0]


def test_a_fifo_is_not_read_and_does_not_block(tmp_path):
    fifo = tmp_path / "traefik.yaml"
    os.mkfifo(fifo)
    located = mounts.locate(
        _workload(mounts.Mount("bind", "/d/traefik.yaml", str(fifo))), "/d/traefik.yaml"
    )

    with pytest.raises(mounts.Unreadable, match="not a regular file"):
        mounts.read_located(located, configs={}, placement=HERE)


def test_a_fifo_is_skipped_in_the_walk_with_a_note(tmp_path):
    os.mkfifo(tmp_path / "pipe.yml")
    workload = _workload(mounts.Mount("bind", "/dyn", str(tmp_path)))

    listing = mounts.provider_files(workload, FileProvider(directory="/dyn"), placement=HERE)

    assert listing.files == []
    assert any("not a regular file" in note for note in listing.notes)


def test_a_nul_byte_in_a_bind_source_does_not_raise_out():
    located = mounts.locate(
        _workload(mounts.Mount("bind", "/d/x.yml", "/tmp/abc\x00def")), "/d/x.yml"
    )

    with pytest.raises(mounts.Unreadable):
        mounts.read_located(located, configs={}, placement=HERE)


def test_a_nul_byte_in_a_bind_directory_source_does_not_raise_out():
    workload = _workload(mounts.Mount("bind", "/dyn", "/tmp/abc\x00def"))

    listing = mounts.provider_files(workload, FileProvider(directory="/dyn"), placement=HERE)

    assert listing.files == []
    assert listing.notes


def test_an_absolute_symlink_is_not_followed_even_when_it_stays_inside(tmp_path):
    root = tmp_path / "dynamic"
    root.mkdir()
    (root / "traefik.yml").write_text("x: 1\n")
    (root / "abs.yml").symlink_to(root / "traefik.yml")
    located = mounts.locate(_workload(mounts.Mount("bind", "/d", str(root))), "/d/abs.yml")

    with pytest.raises(mounts.Unreadable, match="absolute symlink"):
        mounts.read_located(located, configs={}, placement=HERE)


def test_a_relative_symlink_inside_the_bind_source_is_still_followed(tmp_path):
    root = tmp_path / "dynamic"
    root.mkdir()
    (root / "traefik.yml").write_text("x: 1\n")
    (root / "rel.yml").symlink_to("traefik.yml")
    located = mounts.locate(_workload(mounts.Mount("bind", "/d", str(root))), "/d/rel.yml")

    assert mounts.read_located(located, configs={}, placement=HERE) == "x: 1\n"


def test_provider_directory_on_a_volume_is_a_note():
    workload = _workload(mounts.Mount("volume", "/dyn", "dyn_data"))

    listing = mounts.provider_files(workload, FileProvider(directory="/dyn"), placement=HERE)

    assert listing.files == []
    assert listing.notes == ["/dyn is on volume dyn_data — not readable"]


def test_provider_directory_on_a_tmpfs_is_a_note():
    workload = _workload(mounts.Mount("tmpfs", "/dyn", ""))

    listing = mounts.provider_files(workload, FileProvider(directory="/dyn"), placement=HERE)

    assert listing.files == []
    assert listing.notes == ["/dyn is on a tmpfs mount — not readable"]


def test_provider_directory_not_mounted_at_all_is_a_note():
    workload = _workload()

    listing = mounts.provider_files(workload, FileProvider(directory="/dyn"), placement=HERE)

    assert listing.files == []
    assert listing.notes == ["/dyn is not mounted — nothing to read"]


def test_a_symlinked_directory_is_not_descended_into(tmp_path):
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    (real_dir / "secret.yml").write_text("")
    root = tmp_path / "dyn"
    root.mkdir()
    (root / "linked").symlink_to(real_dir)
    workload = _workload(mounts.Mount("bind", "/dyn", str(root)))

    listing = mounts.provider_files(workload, FileProvider(directory="/dyn"), placement=HERE)

    assert listing.files == []


def test_a_config_inside_a_bind_directory_shadows_the_bind_file(tmp_path):
    (tmp_path / "a.yml").write_text("from bind\n")
    workload = _workload(
        mounts.Mount("bind", "/dyn", str(tmp_path)),
        mounts.Mount("config", "/dyn/a.yml", "cfg_a"),
    )

    listing = mounts.provider_files(workload, FileProvider(directory="/dyn"), placement=HERE)

    assert [located.path for located in listing.files] == ["/dyn/a.yml"]
    assert listing.files[0].mount.kind == "config"


def test_dot_dot_in_a_mount_target_is_normalised():
    workload = _workload(mounts.Mount("bind", "/etc/traefik/../secrets", "/srv/secrets"))

    located = mounts.locate(workload, "/etc/secrets/x.yml")

    assert located.host_path == "/srv/secrets/x.yml"


def test_dot_dot_in_the_lookup_path_is_normalised():
    workload = _workload(mounts.Mount("bind", "/etc/traefik", "/srv/traefik"))

    located = mounts.locate(workload, "/etc/traefik/dynamic/../x.yml")

    assert located.host_path == "/srv/traefik/x.yml"


def test_dot_dot_does_not_escape_a_sibling_with_a_similar_name():
    workload = _workload(mounts.Mount("bind", "/etc/traefik", "/srv/traefik"))

    located = mounts.locate(workload, "/etc/traefik/../traefik2/x.yml")

    assert located.mount is None
