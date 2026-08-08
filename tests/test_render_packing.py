from rich.console import Console
from rich.text import Text

from terminal_status_panel.render.packing import PackedColumns, pack_blocks


def _block(height: int, width: int) -> list[Text]:
    return [Text("x" * width) for _ in range(height)]


def test_no_blocks_pack_to_nothing():
    assert pack_blocks([], width=80) == []


def test_a_wide_terminal_gives_every_block_its_own_column():
    blocks = [_block(4, 10) for _ in range(4)]
    # 10 + 4 + 10 + 4 + 10 + 4 + 10 = 52 cells, well inside 100.
    assert pack_blocks(blocks, width=100) == [[0], [1], [2], [3]]


def test_a_narrow_terminal_falls_back_to_one_column():
    blocks = [_block(4, 30) for _ in range(3)]
    # Two columns would need 30 + 4 + 30 = 64.
    assert pack_blocks(blocks, width=40) == [[0, 1, 2]]


def test_the_tall_block_is_balanced_against_the_short_ones():
    blocks = [_block(1, 10), _block(1, 10), _block(1, 10), _block(5, 10)]
    # Only two columns fit in 24 cells. The five-line block takes one of them
    # on its own; the three one-line blocks stack in the other.
    assert pack_blocks(blocks, width=24) == [[3], [0, 1, 2]]


def test_equal_heights_break_ties_by_declaration_order():
    blocks = [_block(4, 20) for _ in range(4)]
    assert pack_blocks(blocks, width=44) == [[0, 2], [1, 3]]


def test_cell_width_decides_not_character_count():
    # Six characters, twelve cells: the verdict glyphs are double width.
    blocks = [[Text("✅" * 6)], [Text("✅" * 6)]]
    assert pack_blocks(blocks, width=20) == [[0, 1]]
    assert pack_blocks(blocks, width=28) == [[0], [1]]


def test_a_block_wider_than_the_terminal_still_packs():
    assert pack_blocks([_block(2, 200)], width=80) == [[0]]


def test_no_block_is_dropped_or_duplicated():
    blocks = [_block(height, 10) for height in (3, 1, 7, 2, 5)]
    columns = pack_blocks(blocks, width=100)
    assert sorted(index for column in columns for index in column) == [0, 1, 2, 3, 4]


def _render(renderable, width: int) -> list[str]:
    console = Console(width=width, force_terminal=False, color_system=None)
    with console.capture() as capture:
        console.print(renderable)
    return capture.get().splitlines()


def test_the_console_width_decides_the_column_count_not_a_configured_one():
    blocks = [[Text("x" * 30)] for _ in range(3)]
    # 30 + 4 + 30 + 4 + 30 = 98 cells: three columns, so one rendered line.
    assert len(_render(PackedColumns(blocks), width=110)) == 1


def test_a_narrow_console_stacks_the_blocks():
    blocks = [[Text("x" * 30)] for _ in range(3)]
    # Two columns would need 64.
    assert len(_render(PackedColumns(blocks), width=50)) == 3


def test_no_rendered_line_exceeds_the_console_width():
    blocks = [[Text("✅" * 20)] for _ in range(4)]
    for width in (40, 60, 100, 200):
        lines = _render(PackedColumns(blocks), width=width)
        assert max(Text(line).cell_len for line in lines) <= width


def test_a_column_stacks_its_blocks_without_a_gap_between_them():
    blocks = [[Text("tall")] * 5, [Text("a")], [Text("b")], [Text("c")]]
    lines = _render(PackedColumns(blocks), width=30)
    # The three one-line blocks share a column and follow one another
    # immediately; the layout is only as tall as the tallest block.
    assert len(lines) == 5


def test_no_blocks_render_to_nothing():
    assert _render(PackedColumns([]), width=80) == []
