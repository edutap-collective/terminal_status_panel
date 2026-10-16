import json
from dataclasses import asdict
from pathlib import Path

import pytest

from terminal_status_panel.collector import vrrp

FIX = Path(__file__).parent / "fixtures" / "host"
READ_FIELDS = {"name", "state", "base_priority", "effective_priority", "master_priority"}


def test_parses_v1_master_fixture():
    instances = vrrp.parse_keepalived_json((FIX / "keepalived-v1-master.json").read_text())
    assert instances[0].name == "vi_1" and instances[0].state == "MASTER"
    assert instances[0].base_priority is not None and instances[0].effective_priority is not None


def test_parses_v1_backup_fixture_with_master_priority():
    [inst] = vrrp.parse_keepalived_json((FIX / "keepalived-v1-backup.json").read_text())
    assert inst.state == "BACKUP" and inst.master_priority == 200
    assert inst.lead == -10


def test_backup_fixture_leaks_no_field_beyond_the_five_read_ones():
    [inst] = vrrp.parse_keepalived_json((FIX / "keepalived-v1-backup.json").read_text())
    assert set(asdict(inst)) == READ_FIELDS
    assert "192.0.2.10" not in repr(inst) and "stats" not in repr(inst)


def test_parses_v2_object_and_maps_state_numbers():
    text = json.dumps(
        {
            "vrrp": [
                {
                    "data": {
                        "iname": "vi_2",
                        "state": 1,
                        "base_priority": 190,
                        "effective_priority": 188,
                        "master_priority": 196,
                        "auth_data": "secret",
                    },
                    "stats": {},
                }
            ]
        }
    )
    [inst] = vrrp.parse_keepalived_json(text)
    assert (inst.name, inst.state, inst.lead) == ("vi_2", "BACKUP", -8)


def test_secrets_never_leave_the_parser():
    text = json.dumps(
        [
            {
                "data": {
                    "iname": "vi_1",
                    "state": 2,
                    "base_priority": 1,
                    "effective_priority": 1,
                    "master_priority": 1,
                    "auth_data": "secret",
                }
            }
        ]
    )
    assert "secret" not in repr(vrrp.parse_keepalived_json(text))


def test_garbage_raises():
    with pytest.raises(ValueError):
        vrrp.parse_keepalived_json("[{]")


def test_entry_without_data_object_raises():
    with pytest.raises(ValueError):
        vrrp.parse_keepalived_json('[{"data": "not an object"}]')


def test_dump_is_deleted_after_reading(tmp_path):
    dump = tmp_path / "keepalived.json"
    dump.write_text((FIX / "keepalived-v1-master.json").read_text())
    state, error = vrrp.collect_vrrp(dump)
    assert error is None and state.source == "keepalived-json" and not dump.exists()


def test_dump_is_deleted_even_when_it_does_not_parse(tmp_path):
    dump = tmp_path / "keepalived.json"
    dump.write_text("[{]")
    state, error = vrrp.collect_vrrp(dump)
    assert state is None and error == "parse" and not dump.exists()


def test_no_dump_is_not_installed(tmp_path):
    state, error = vrrp.collect_vrrp(tmp_path / "absent.json")
    assert state is None and error == "not-installed"


def test_dump_that_is_not_utf8_is_a_parse_error_and_is_deleted(tmp_path):
    dump = tmp_path / "keepalived.json"
    dump.write_bytes(b"\xff\xfe[{")
    state, error = vrrp.collect_vrrp(dump)
    assert state is None and error == "parse" and not dump.exists()


def test_v2_object_without_vrrp_list_is_rejected():
    with pytest.raises(ValueError):
        vrrp.parse_keepalived_json('{"other": []}')


def test_failed_delete_keeps_the_state_and_reports_cleanup(tmp_path, monkeypatch):
    dump = tmp_path / "keepalived.json"
    dump.write_text((FIX / "keepalived-v1-master.json").read_text())

    def refuse(self, missing_ok=False):
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "unlink", refuse)
    state, error = vrrp.collect_vrrp(dump)
    assert error == "cleanup"
    assert state is not None and state.instances[0].name == "vi_1"
