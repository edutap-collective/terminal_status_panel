# terminal-status-panel

A colourful server status panel for a login shell. It reads the machine it runs
on — system, resources, Docker and Swarm, clustered services, Traefik's wiring —
and renders one dashboard sized to the terminal it is printed into.

It is a diagnostic tool, so it never fails a login: the command always exits 0,
a collector that cannot answer degrades to a placeholder, and a check that runs
out of its time budget says so rather than guessing.

```shell
pip install terminal-status-panel
status-full
```

## The commands

| Command | Renders |
|---------|---------|
| `status-full` | everything: system, Docker, cluster health, Traefik wiring |
| `status-server` | system and resources only |
| `status-docker` | the Docker and Swarm block only |
| `status-health` | the cluster, network and DNS checks only |
| `status-traefik` | the Traefik wiring view only |
| `install-panel` | install or remove the login hook |

Every one of them takes `--width`, `--no-color`, `--config`, `--follow`,
`--interval`, `--processes` and `--debug`. See
[commands and command-line options](docs/reference/commands.md).

## What it shows

- **SYSTEM OVERVIEW** and **UPDATES** — OS, kernel, uptime, addresses, pending
  Debian/Ubuntu package updates, beside a pre-rendered OS logo.
- **SYSTEM STATUS** — load, CPU per core, memory, swap, filesystems, and the
  processes ranked by CPU and by memory
  ([about the process lists](docs/explanation/top-processes.md)).
- **DOCKER INFOS** — Swarm facts, node health, and a matrix of services per
  node, with the image each runs, the memory its tasks hold, and a TROUBLE
  block for anything that fell over recently
  ([about the Docker panel](docs/explanation/docker-panel.md)).
- **CLUSTER HEALTH** — read-only probes of PostgreSQL, MongoDB, Kafka,
  GlusterFS and RustFS, plus WireGuard peers and DNS consistency
  ([about the cluster health checks](docs/explanation/cluster-health.md)).
- **TRAEFIK WIRING** — entrypoint → router → middleware → service,
  reconstructed from Docker labels and the file provider
  ([about the Traefik wiring view](docs/explanation/traefik-wiring.md)).

## The status vocabulary

| Icon | Meaning |
|------|---------|
| ✅ | measured healthy |
| ⚠️ | degraded, but serving |
| 💀 | measured broken |
| ⏰ | a scheduled job, resting between successful runs |
| ⬜ | not observable, or not attempted |
| `…` | the check ran out of the shared time budget |
| `✗` | the check itself failed |

`⬜`, `…` and `✗` are three different statements and are never conflated. The
full table is in the [icon vocabulary](docs/reference/icon-vocabulary.md).

## What it may do to your machine

The panel opens no database or broker connection and holds no credentials. Its
only privilege is the Docker socket: the Docker section reads the Swarm API,
and the health section additionally executes **read-only status commands inside
the service containers** (`pg_autoctl show state`, `db.hello()`,
`kafka-metadata-quorum.sh`, a `/health` curl). GlusterFS is queried on the host
via `sudo -n` and is skipped when that is unavailable.

## Documentation

The full documentation lives in [`docs/`](docs/index.md) and builds with Sphinx
(`make docs`).

- [Run the panel for the first time](docs/tutorials/first-run.md) — start here.
- [Run the panel at login](docs/how-to/run-it-at-login.md) — the `profile.d`
  snippet, and why not `update-motd.d`.
- [Configuration](docs/reference/configuration.md) — every key, its default and
  what it does.
- [Diagnose an empty panel](docs/how-to/diagnose-an-empty-panel.md) — what
  `--debug` reports and how to read it.
- [Requirements and stability](docs/reference/stability.md) — supported
  platforms, and why there is no public Python API before 1.0.

Changes are recorded in [CHANGELOG.md](CHANGELOG.md).

## License

Licensed under the [EUPL-1.2](LICENSE).
