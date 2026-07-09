from lmu.terminal_status_panel.data.mem_data import get_mem_data


def test_get_mem_data():
    assert get_mem_data() == "20GB"