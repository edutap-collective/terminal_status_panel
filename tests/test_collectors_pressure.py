from terminal_status_panel.collectors.pressure import read_pressure


def test_reads_some_avg10_and_avg60(tmp_path):
    (tmp_path / "cpu").write_text(
        "some avg10=9.11 avg60=8.39 avg300=7.34 total=1\n"
        "full avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"
    )
    (tmp_path / "memory").write_text("some avg10=0.00 avg60=0.10 avg300=0.00 total=1\n")
    assert read_pressure(str(tmp_path)) == {"cpu": (9.11, 8.39), "memory": (0.0, 0.1)}


def test_absent_directory_is_empty_not_an_error(tmp_path):
    assert read_pressure(str(tmp_path / "nope")) == {}


def test_malformed_file_is_skipped(tmp_path):
    (tmp_path / "cpu").write_text("garbage\n")
    assert read_pressure(str(tmp_path)) == {}
