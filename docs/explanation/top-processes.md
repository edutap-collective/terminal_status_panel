# About the process lists

The SYSTEM STATUS block ends with two tables side by side, **TOP CPU** and
**TOP RAM**, each with `%CPU`, `%MEM`, `MEM`, `PID`, `PROCESS` and `SERVICE`
columns — five rows per table by default.

**`MEM` is the resident set size, the same figure `%MEM` is a percentage
of.** psutil's `memory_percent()` is computed from `rss`, and `MEM` shows
that same `rss` value, formatted with the helper the MEMORY & SWAP block
above uses (`format_bytes`), so a size reads the same everywhere in the
panel — `2.0 GB` here is the same `2.0 GB` it would be up there. A process
the collector could not read carries a dash, `—`, in both columns rather
than mixing that with `format_bytes`'s own `n/a` for one row.

**Row count: `[resources] top_processes`, or `--processes N` on the command
line.** The default is `5`; the flag wins whenever both are given. A config
value that cannot be read as a whole number falls back to `5`, the same as
an unset one; a negative value — from either source — means `0`, not the
default.

**`0` turns the block off, and with it the CPU-sampling wait.** With
`--processes 0` (or `top_processes = 0`), the TOP CPU / TOP RAM row does not
render at all, and the collector skips the sampling window described below
entirely rather than measuring it and discarding the result — that window is
real cost on a login path, and removing it is the reason this knob exists.
Measured on a development machine, `--processes 3` took about 0.69 s wall
clock; `--processes 0` took about 0.29 s.

**`top_processes` and `process_sample` are different switches, and they do
different things at zero.** A row count of `0` removes the whole block,
sampling wait included. A sample of `0` (or less) leaves the block in place
and turns off only the CPU ranking: **TOP CPU** then reads
`CPU sampling is off` and **TOP RAM** renders alone. Setting the wrong one
either keeps paying the sampling cost while meaning to stop it, or drops
**TOP RAM** along with **TOP CPU** while meaning to keep it.

**`%CPU` is sampled over a window, not the lifetime average `ps` reports.**
`ps -eo %cpu` divides total CPU time by elapsed time since the process
started, so a container that has been running for weeks barely moves
whatever it is doing right now. This panel primes every process, waits, and
reads instead, so the figure is the share of CPU actually used during that
window — and the `TOP CPU` heading names the window it used, e.g.
`TOP CPU (0.3s)`.

That window is the `[resources] process_sample` config key, `0.3` seconds by
default, and it is real cost on a login path: sampling roughly 400 processes
measured at 0.32 s wall clock on a five-node reference cluster. Set it to `0`
or less and the CPU ranking turns off entirely — the row then reads
`CPU sampling is off` in place of a table, rather than rows of `0.0` that
would read as a measurement rather than its absence — and **TOP RAM** alone
remains.

**`SERVICE` is read from `/proc/<pid>/cgroup`.** A process running under a
systemd unit shows that unit's name verbatim. A process running inside a
container shows the container's short ID, and that ID resolves to a service
name only when the Docker section was also collected and can map it — which
is why `status-server` on its own shows IDs rather than names: it never
opens the Docker socket, deliberately, so it has nothing to resolve the ID
against.

**On a narrow terminal, the two tables stack instead of squeezing.** The
panel measures each table's natural width and lays `TOP CPU` beside
`TOP RAM`, with a gap between them, only when both fit the terminal as they
are — verified at width 200 and at 120, where both render in full side by
side, with only `SERVICE`'s own 22-character cap ever cutting a long unit or
service name (`containerd-shim`, unremarkable on a Docker host, reads in
full at either width). Once the pair no longer fits — verified at width 80,
which is `Config.width`'s default and what `resolve_width` falls back to
whenever stdout is not a TTY, the MOTD-generation path this README already
names — `TOP RAM` moves below `TOP CPU` instead of beside it, each keeping
the full terminal width and its own heading. Rich is never asked to shrink a
pair that does not fit; a pair that does not fit is stacked instead. That is
why the numeric columns — `%CPU`, `%MEM`, `MEM`, `PID` — keep their values
undamaged at every width, which is the guarantee worth making: a shortened
name is still shorter, but a shortened number would simply be wrong.

The panel excludes its own process from both rankings — the same reason
`ps` habitually ranks itself first: it is the one process guaranteed to be
running while the measurement happens.
