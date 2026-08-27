# About follow mode

`-f` / `--follow` keeps the panel on screen and redraws it on an interval,
on all five commands, in place of a single one-shot render.

**The default interval depends on which sections are collected, not on which
command you ran.** If `health` is among the requested sections, the default
is 20 s (config key `[follow] health_interval`); otherwise it is 5 s (`[follow]
interval`). That is a rule about sections rather than a per-command table, so
it is also correct for a combination like `--sections docker,health` on
`status-full`. `--interval N` on the command line overrides both.

The health section earns the longer default: it runs `docker exec` probes
inside the cluster's containers, and the Kafka probe alone carries roughly
2.6 s of JVM startup (see {doc}`Cluster health checks </explanation/cluster-health>`).
Measured on a five-node reference cluster at width 200, median of three runs:
`status-health` takes 3.43 s per pass against `status-server`'s 0.49 s — a
5 s cadence would start a new JVM on the cluster every five seconds, forever.

**Ctrl-C stops it.** There is no `q` key or other in-panel control; reading a
single keypress would need raw mode, and Ctrl-C already does the job.

**The panel is cropped to the screen**, with a status line at the bottom
naming what does not fit — `↓ 82 more lines · every 20s · Ctrl-C to stop` —
and the `↓` clause absent once everything fits. On the same reference
cluster, `status-full` renders 131 lines at width 200, taller than a normal
terminal, so it will be cropped there; each of the four section commands
(22 to 51 lines) fits on an ordinary screen.

**Without a TTY, `--follow` renders one frame and returns**, the same as a
plain run — piping the output to a file or generating a cached MOTD still
works, rather than looping forever inside a pipe.
