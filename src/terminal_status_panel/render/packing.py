"""Arrange pre-rendered blocks of lines into height-balanced columns.

``rich.Columns`` fills row by row, and a row is as tall as its tallest cell: a
three-line block beside a seventeen-line one leaves fourteen blank lines
behind. Its ``column_first`` mode does fill column by column, but it splits by
item count rather than by height, which for blocks this uneven is worse still.
Balancing by height needs a packer of our own.

Blocks arrive as sequences of ``Text`` lines rather than as renderables, so
height and width are known by looking rather than by a trial render.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from rich.console import Console, ConsoleOptions, Group, RenderResult
from rich.table import Table
from rich.text import Text


def block_width(block: Sequence[Text]) -> int:
    """The block's width in terminal cells.

    Cells, not characters. ``✅`` and ``💀`` are one character each and occupy
    two columns of the terminal; measuring them with ``len`` makes every
    computed column too narrow, and the rightmost one then wraps into the
    column beside it. This is the one way this module goes quietly wrong.
    """
    return max((line.cell_len for line in block), default=0)


def _distribute(heights: Sequence[int], count: int) -> list[list[int]]:
    """Assign every block to one of *count* columns, tallest block first.

    Largest-first into the currently shortest column is the classic
    longest-processing-time rule, and on the shapes this panel produces — one
    block much taller than the rest — it reaches the optimum. Both tie-breaks
    are on index, so the same input always yields the same layout: a panel
    whose columns reshuffled between two runs of the same data would be worse
    than a ragged one.
    """
    order = sorted(range(len(heights)), key=lambda index: (-heights[index], index))
    columns: list[list[int]] = [[] for _ in range(count)]
    filled = [0] * count
    for index in order:
        target = min(range(count), key=lambda column: (filled[column], column))
        columns[target].append(index)
        filled[target] += heights[index]
    return [sorted(column) for column in columns if column]


def pack_blocks(
    blocks: Sequence[Sequence[Text]], width: int, gap: int = 4
) -> list[list[int]]:
    """Group *blocks* into as many columns as *width* holds, balanced by height.

    Returns one list of block indices per column, each in declaration order, so
    the section reads column by column from the top. Column widths are
    independent: each column is as wide as its own widest block.

    The column count is searched from many to few and the first fit wins, which
    is the widest arrangement and therefore the shortest. One column always
    fits — a block wider than the terminal is left to wrap, as it did before —
    so the search never comes back empty.
    """
    if not blocks:
        return []
    heights = [len(block) for block in blocks]
    widths = [block_width(block) for block in blocks]
    for count in range(len(blocks), 1, -1):
        columns = _distribute(heights, count)
        used = sum(max(widths[index] for index in column) for column in columns)
        if used + gap * (len(columns) - 1) <= width:
            return columns
    return [list(range(len(blocks)))]


class PackedColumns:
    """The packed layout, as a renderable that measures at print time.

    The width is taken from the console options rather than from ``Config``.
    ``Config.width`` is the fallback for a session with no terminal attached;
    when there is one, ``cli.resolve_width`` hands the real width to the
    ``Console`` and never writes it back. Packing against the config would mean
    packing against 80 columns on a 215-column terminal, which is the bug this
    class exists to avoid.
    """

    def __init__(self, blocks: Iterable[Sequence[Text]], gap: int = 4) -> None:
        self.blocks = list(blocks)
        self.gap = gap

    def __rich_console__(
        self, console: Console, options: ConsoleOptions
    ) -> RenderResult:
        columns = pack_blocks(self.blocks, options.max_width, self.gap)
        if not columns:
            return
        # collapse_padding and pad_edge match what rich.Columns sets, so the
        # section keeps the left edge and the gutter it has today.
        grid = Table.grid(padding=(0, self.gap), collapse_padding=True, pad_edge=False)
        for _ in columns:
            grid.add_column()
        cells = []
        for column in columns:
            lines: list[Text] = []
            for index in column:
                lines.extend(self.blocks[index])
            cells.append(Group(*lines))
        grid.add_row(*cells)
        yield grid
