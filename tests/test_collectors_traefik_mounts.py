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
    # A relative escape (".." past the source root) -- an *absolute*-target
    # symlink is refused for a different reason ("is an absolute symlink"),
    # covered separately; see the fix-round-3 tests below.
    (root / "link.yml").symlink_to("../secret.yml")
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


# --- Fix round 2 -------------------------------------------------------


def test_an_absolute_symlink_on_an_intermediate_directory_is_not_followed(tmp_path):
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    (real_dir / "t.yml").write_text("x: 1\n")
    (tmp_path / "absdir").symlink_to(real_dir)
    located = mounts.locate(_workload(mounts.Mount("bind", "/d", str(tmp_path))), "/d/absdir/t.yml")

    with pytest.raises(mounts.Unreadable, match="absolute symlink"):
        mounts.read_located(located, configs={}, placement=HERE)


def test_a_relative_symlink_chaining_to_an_absolute_one_is_not_followed(tmp_path):
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    (real_dir / "t.yml").write_text("x: 1\n")
    (tmp_path / "abs.yml").symlink_to(real_dir / "t.yml")
    (tmp_path / "chain.yml").symlink_to("abs.yml")
    located = mounts.locate(_workload(mounts.Mount("bind", "/d", str(tmp_path))), "/d/chain.yml")

    with pytest.raises(mounts.Unreadable, match="absolute symlink"):
        mounts.read_located(located, configs={}, placement=HERE)


def test_a_bind_file_source_that_is_itself_an_absolute_symlink_is_read_normally(tmp_path):
    (tmp_path / "release.yml").write_text("entryPoints: {}\n")
    (tmp_path / "traefik.yml").symlink_to(tmp_path / "release.yml")
    workload = _workload(
        mounts.Mount("bind", "/etc/traefik/traefik.yml", str(tmp_path / "traefik.yml"))
    )
    located = mounts.locate(workload, "/etc/traefik/traefik.yml")

    assert mounts.read_located(located, configs={}, placement=HERE) == "entryPoints: {}\n"


def test_a_nested_bind_under_an_unsearchable_parent_gives_a_note_not_unmounted(tmp_path):
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("permission bits do not apply to root")
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "extra").mkdir()
    (locked / "extra" / "a.yml").write_text("")
    locked.chmod(0)
    try:
        workload = _workload(mounts.Mount("bind", "/dyn/extra", str(locked / "extra")))
        listing = mounts.provider_files(workload, FileProvider(directory="/dyn"), placement=HERE)

        assert listing.files == []
        assert any("/dyn/extra" in note for note in listing.notes)
        assert not any("not mounted" in note for note in listing.notes)
    finally:
        locked.chmod(0o700)


def test_a_missing_nested_bind_source_gives_a_note_with_the_host_path(tmp_path):
    missing = tmp_path / "missing"
    workload = _workload(mounts.Mount("bind", "/dyn/extra", str(missing)))

    listing = mounts.provider_files(workload, FileProvider(directory="/dyn"), placement=HERE)

    assert listing.files == []
    assert any(str(missing) in note for note in listing.notes)


def test_a_nul_byte_in_a_nested_bind_source_does_not_raise_out():
    workload = _workload(mounts.Mount("bind", "/dyn/sub", "/tmp/abc\x00def"))

    listing = mounts.provider_files(workload, FileProvider(directory="/dyn"), placement=HERE)

    assert listing.files == []
    assert listing.notes


def test_a_genuinely_empty_nested_bind_directory_is_not_reported_as_unmounted(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    workload = _workload(mounts.Mount("bind", "/dyn/sub", str(empty)))

    listing = mounts.provider_files(workload, FileProvider(directory="/dyn"), placement=HERE)

    assert listing.files == []
    assert listing.notes == []


# --- Fix round 3 -------------------------------------------------------


def test_a_relative_target_through_an_absolute_intermediate_link_is_not_followed(tmp_path):
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    (real_dir / "t.yml").write_text("REAL\n")
    (tmp_path / "absdir").symlink_to(real_dir)
    (tmp_path / "via.yml").symlink_to("absdir/t.yml")
    located = mounts.locate(_workload(mounts.Mount("bind", "/d", str(tmp_path))), "/d/via.yml")

    with pytest.raises(mounts.Unreadable, match="absolute symlink"):
        mounts.read_located(located, configs={}, placement=HERE)


def test_an_absolute_link_nested_inside_a_relative_targets_own_target_is_not_followed(tmp_path):
    real_dir = tmp_path / "real"
    (real_dir / "deep").mkdir(parents=True)
    (real_dir / "deep" / "u.yml").write_text("DEEP\n")
    (tmp_path / "absdir").symlink_to(real_dir)
    (tmp_path / "reldir").symlink_to("absdir/deep")
    located = mounts.locate(_workload(mounts.Mount("bind", "/d", str(tmp_path))), "/d/reldir/u.yml")

    with pytest.raises(mounts.Unreadable, match="absolute symlink"):
        mounts.read_located(located, configs={}, placement=HERE)


def test_dot_dot_after_a_symlinked_component_resolves_physically_not_lexically(tmp_path):
    real_dir = tmp_path / "real"
    (real_dir / "deep").mkdir(parents=True)
    (real_dir / "t.yml").write_text("REAL\n")
    # The kernel's target once "dl" is followed: real/deep/../t2.yml == real/t2.yml
    # (an absolute symlink) -- not the *lexical* tmp_path/t2.yml decoy below, which
    # a naive component-cancelling normpath of "dl/../t2.yml" would land on instead.
    (real_dir / "t2.yml").symlink_to(real_dir / "t.yml")
    (tmp_path / "t2.yml").write_text("DECOY\n")
    (tmp_path / "dl").symlink_to("real/deep")
    (tmp_path / "lex.yml").symlink_to("dl/../t2.yml")
    located = mounts.locate(_workload(mounts.Mount("bind", "/d", str(tmp_path))), "/d/lex.yml")

    with pytest.raises(mounts.Unreadable, match="absolute symlink"):
        mounts.read_located(located, configs={}, placement=HERE)


def test_a_relative_hop_that_climbs_above_the_source_and_back_in_is_refused(tmp_path):
    source = tmp_path / "S"
    (source / "real").mkdir(parents=True)
    (source / "real" / "t.yml").write_text("REAL\n")
    (source / "climb.yml").symlink_to("../S/real/t.yml")
    located = mounts.locate(_workload(mounts.Mount("bind", "/d", str(source))), "/d/climb.yml")

    with pytest.raises(mounts.Unreadable, match="leads outside"):
        mounts.read_located(located, configs={}, placement=HERE)


def test_a_relative_hop_back_to_the_source_root_is_followed_even_when_the_source_is_a_symlink(
    tmp_path,
):
    real_root = tmp_path / "R"
    (real_root / "sub").mkdir(parents=True)
    (real_root / "t.yml").write_text("ROOT-T\n")
    (real_root / "sub" / "up").symlink_to("..")
    root_link = tmp_path / "rootlink"
    root_link.symlink_to(real_root)
    located = mounts.locate(
        _workload(mounts.Mount("bind", "/d", str(root_link))), "/d/sub/up/t.yml"
    )

    assert mounts.read_located(located, configs={}, placement=HERE) == "ROOT-T\n"


def test_a_relative_self_reference_is_followed_even_when_the_source_is_a_symlink(tmp_path):
    real_root = tmp_path / "R"
    real_root.mkdir()
    (real_root / "t.yml").write_text("ROOT-T\n")
    (real_root / "cur").symlink_to(".")
    root_link = tmp_path / "rootlink"
    root_link.symlink_to(real_root)
    located = mounts.locate(_workload(mounts.Mount("bind", "/d", str(root_link))), "/d/cur/t.yml")

    assert mounts.read_located(located, configs={}, placement=HERE) == "ROOT-T\n"


def test_a_symlink_loop_at_the_final_component_is_refused_without_hanging(tmp_path):
    (tmp_path / "a.yml").symlink_to("b.yml")
    (tmp_path / "b.yml").symlink_to("a.yml")
    located = mounts.locate(_workload(mounts.Mount("bind", "/d", str(tmp_path))), "/d/a.yml")

    with pytest.raises(mounts.Unreadable, match="too many levels"):
        mounts.read_located(located, configs={}, placement=HERE)


def test_a_symlink_loop_at_an_intermediate_component_is_refused_without_hanging(tmp_path):
    (tmp_path / "d1").symlink_to("d2")
    (tmp_path / "d2").symlink_to("d1")
    located = mounts.locate(_workload(mounts.Mount("bind", "/d", str(tmp_path))), "/d/d1/t.yml")

    with pytest.raises(mounts.Unreadable, match="too many levels"):
        mounts.read_located(located, configs={}, placement=HERE)


# --- Fix round 4 -------------------------------------------------------


def _read(workload, path):
    return mounts.read_located(mounts.locate(workload, path), configs={}, placement=HERE)


def test_a_walked_link_climbing_out_of_the_provider_directory_but_not_the_mount_is_listed(
    tmp_path,
):
    # Inside the container, /dyn/sub/x.yml -> ../shared.yml is /dyn/shared.yml:
    # still inside the bind mount, so Traefik loads it.
    (tmp_path / "shared.yml").write_text("SHARED\n")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "own.yml").write_text("OWN\n")
    (tmp_path / "sub" / "x.yml").symlink_to("../shared.yml")
    workload = _workload(mounts.Mount("bind", "/dyn", str(tmp_path)))

    listing = mounts.provider_files(workload, FileProvider(directory="/dyn/sub"), placement=HERE)

    assert [located.path for located in listing.files] == ["/dyn/sub/own.yml", "/dyn/sub/x.yml"]
    assert listing.notes == []
    assert _read(workload, "/dyn/sub/x.yml") == "SHARED\n"


def test_a_provider_directory_that_is_a_relative_link_to_a_sibling_is_listed(tmp_path):
    (tmp_path / "shared.yml").write_text("SHARED\n")
    (tmp_path / "sibling").mkdir()
    (tmp_path / "sibling" / "own.yml").write_text("OWN\n")
    (tmp_path / "sibling" / "x.yml").symlink_to("../shared.yml")
    (tmp_path / "sub").symlink_to("sibling")
    workload = _workload(mounts.Mount("bind", "/dyn", str(tmp_path)))

    listing = mounts.provider_files(workload, FileProvider(directory="/dyn/sub"), placement=HERE)

    assert [located.path for located in listing.files] == ["/dyn/sub/own.yml", "/dyn/sub/x.yml"]
    assert listing.notes == []
    assert _read(workload, "/dyn/sub/own.yml") == "OWN\n"
    assert _read(workload, "/dyn/sub/x.yml") == "SHARED\n"


def test_the_walk_and_a_read_agree_on_an_absolute_link_above_the_provider_directory(tmp_path):
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    (real_dir / "t.yml").write_text("REAL\n")
    (tmp_path / "sub").symlink_to(real_dir)
    workload = _workload(mounts.Mount("bind", "/dyn", str(tmp_path)))

    listing = mounts.provider_files(workload, FileProvider(directory="/dyn/sub"), placement=HERE)

    assert listing.files == []
    assert len(listing.notes) == 1
    assert listing.notes[0].startswith("/dyn/sub/t.yml: ")
    assert "absolute symlink" in listing.notes[0]
    with pytest.raises(mounts.Unreadable, match="absolute symlink"):
        _read(workload, "/dyn/sub/t.yml")


def test_a_walked_entry_that_cannot_be_resolved_is_a_note_and_the_rest_is_listed(tmp_path):
    (tmp_path / "a.yml").write_text("")
    (tmp_path / "loop.yml").symlink_to("loop.yml")
    (tmp_path / "z.yml").write_text("")
    workload = _workload(mounts.Mount("bind", "/dyn", str(tmp_path)))

    listing = mounts.provider_files(workload, FileProvider(directory="/dyn"), placement=HERE)

    assert [located.path for located in listing.files] == ["/dyn/a.yml", "/dyn/z.yml"]
    assert len(listing.notes) == 1
    assert listing.notes[0].startswith("/dyn/loop.yml: ")
    assert "too many levels of symbolic links" in listing.notes[0]


def _vanished(path, *args, **kwargs):
    """``os.readlink`` as it fails when the link disappears right after ``lstat``."""
    raise FileNotFoundError(2, "No such file or directory", path)


def test_a_link_that_vanishes_during_a_read_is_unreadable_not_an_os_error(tmp_path, monkeypatch):
    (tmp_path / "t.yml").write_text("")
    (tmp_path / "rel.yml").symlink_to("t.yml")
    workload = _workload(mounts.Mount("bind", "/d", str(tmp_path)))
    monkeypatch.setattr(os, "readlink", _vanished)

    with pytest.raises(mounts.Unreadable, match="No such file or directory"):
        _read(workload, "/d/rel.yml")


def test_a_link_that_vanishes_during_the_walk_is_a_note_for_that_entry_only(tmp_path, monkeypatch):
    (tmp_path / "a.yml").write_text("")
    (tmp_path / "rel.yml").symlink_to("a.yml")
    (tmp_path / "z.yml").write_text("")
    workload = _workload(mounts.Mount("bind", "/dyn", str(tmp_path)))
    monkeypatch.setattr(os, "readlink", _vanished)

    listing = mounts.provider_files(workload, FileProvider(directory="/dyn"), placement=HERE)

    assert [located.path for located in listing.files] == ["/dyn/a.yml", "/dyn/z.yml"]
    assert len(listing.notes) == 1
    assert listing.notes[0].startswith("/dyn/rel.yml: ")
    assert "No such file or directory" in listing.notes[0]


def test_a_bind_mount_with_an_empty_source_is_unreadable_not_a_value_error():
    workload = _workload(mounts.Mount("bind", "/d", ""))

    with pytest.raises(mounts.Unreadable, match="empty source"):
        _read(workload, "/d")
