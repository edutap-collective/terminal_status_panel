# Configuration

Zero configuration is required. Settings are read from
`/etc/terminal-status-panel/config.toml` (override with `--config PATH`). A missing
or unreadable file falls back to the built-in defaults — it never raises.

## Configuration reference

| Key | Default | Meaning |
|-----|---------|---------|
| `width` | `80` | Fallback render width when no TTY is available (see width resolution above). |
| `docker.timeout` | `1.5` | Seconds to wait for the Docker socket before giving up (also bounds the `apt` update check). Keeps a hung/absent daemon from delaying login. |
| `docker.description_label` | `"status.description"` | Docker **service label** read as the per-service description column. The key `lmu.service.description` is still read as a fallback. |
| `docker.group_label` | `"status.group"` | Docker **service label** naming the row a service belongs in. Services of one stack sharing a value render as one row, whatever their names. Where it is absent the name heuristic decides instead — see {doc}`Grouping services into one row </explanation/docker-panel>`. Read for presence, not truthiness: a service setting it to `""` groups with no one. |
| `docker.df_timeout` | `4.0` | Seconds to wait for `/system/df`, the Docker disk reading — deliberately larger than `docker.timeout` and spent on a **separate client**. The call was measured at 510 ms against a daemon holding 47 images and 185 volumes, and it grows with the object count. Overrunning it costs the one line, never the whole DOCKER INFOS section. See {doc}`Docker\'s own disk footprint </explanation/docker-panel>`. |
| `docker.show_image` | `true` | Whether the DOCKER INFOS rows carry an **Image** column right of the description (see {doc}`The image column </explanation/docker-panel>`). It is the column that answers "which version is deployed here", and the one that costs the description its width on a narrow terminal — `false` removes it. |
| `resources.ignore_mountpoints` | platform-dependent | Mountpoint prefixes hidden from the filesystem table. Defaults to `["/System/Volumes/", "/Library/Developer/CoreSimulator/"]` on macOS and to `[]` elsewhere. An explicitly empty list hides nothing rather than falling back to the default. |
| `resources.process_sample` | `0.3` | Seconds to sample process CPU usage over for the TOP CPU row (see {doc}`Top processes </explanation/top-processes>`). `0` or less disables the CPU ranking; TOP RAM is unaffected. |
| `resources.top_processes` | `5` | Rows per process table in the TOP CPU / TOP RAM row (see {doc}`Top processes </explanation/top-processes>`). `--processes N` on the command line wins over this. A value that cannot be read as a whole number falls back to `5`; a negative value means `0`. `0` removes the whole row, and with it the `process_sample` sampling wait — a different switch from `process_sample`, which only removes the CPU ranking and leaves TOP RAM in place. |
| `docker.infrastructure_stacks` | `["postgresql", "postgres", "kafka", "mongodb", "rustfs", "portainer", "traefik", "registry", "minio", "redis", "valkey", "mariadb", "mysql", "elasticsearch", "bugsink", "swarm-cronjob", "swarm_cronjob"]` | Case-insensitive substrings. A **stack** (or Compose project) whose name matches goes into that origin's **Infrastructure** table; every other stack goes into **Service**. An entry with no stack at all is never classified this way — it has no project to be filed under, so `docker run -d redis` lands in **Standalone containers** like any other stackless entry, however infrastructural its name. |
| `docker.infra_ui_services` | `["kafbat-ui", "kafka-ui", "kafdrop", "cloudbeaver", "pgadmin", "adminer", "mongo-express", "mongo-gui", "rustfs-console", "rustfs-ui", "s3-browser", "s3browser", "redisinsight", "redis-commander", "portainer", "dozzle", "kibana"]` | Case-insensitive substrings matched against the stack name **and** the service name. Matching services leave their own stack and are collected as sub-rows of the pseudo stack **`infra-uis`**, shown first in the **Infrastructure** block. On a name matching both lists, this one wins. A sidecar pulled in only because its *stack* name matched (e.g. `portainer_agent`) is labelled `stack/service` so it stays attributable once detached. |
| `services.critical` | `[]` | Service names flagged as critical (parsed and available on the data model; not visually emphasised in the current matrix view). |
| `thresholds.memory.warning` / `.critical` | `75` / `90` | RAM usage % thresholds (yellow / red). |
| `thresholds.swap.warning` | platform-dependent | Swap usage % above which SWAP turns yellow. Defaults to `80` on macOS, which allocates swap continuously by design, and to `1` elsewhere. An explicit value overrides both. |
| `thresholds.filesystem.warning` / `.critical` | `80` / `90` | Filesystem usage % thresholds. |
| `thresholds.load.warning` / `.critical` | `0.8` / `1.0` | Load-average thresholds as a **per-CPU multiplier** (compared against `load1 / cpu_count`). |
| `health.budget` | `8.0` | Total wall-clock budget in seconds for all health checks. Every check runs concurrently as its own task, so this bounds the login delay — it is not the sum of the individual timeouts. |
| `health.timeout.*` | postgres `1.5`, mongodb `6.0`, kafka `4.0`, glusterfs `1.0`, rustfs `2.0`, wireguard `1.0`, dns `2.5` | Deadline for one check. Each cluster kind, the peer check and the DNS check are separate tasks; a task that overruns its value is reported as `… <name>: time budget exceeded` while every other check keeps its result. Values above `health.budget` have no effect — the budget always wins. See {doc}`How the timeouts are enforced </explanation/cluster-health>`. |
| `health.enabled` | all five kinds | Which cluster kinds to probe: `postgres`, `mongodb`, `kafka`, `glusterfs`, `rustfs`. |
| `health.dns.expect` | `[]` | Array of `{name, addresses}`. `addresses` is optional; without it the name only has to resolve at all. |
| `follow.interval` | `5.0` | Refresh interval in seconds for `--follow` when the `health` section is **not** among those requested (see {doc}`Follow mode </explanation/follow-mode>`). |
| `follow.health_interval` | `20.0` | Refresh interval in seconds for `--follow` when the `health` section **is** among those requested. |
| `traefik.url` | *(unset)* | URL of Traefik's `/api/rawdata` endpoint for the optional live cross-check. Leave unset — see {doc}`Traefik wiring </explanation/traefik-wiring>` for why it cannot work on today's app servers. |
| `traefik.cert` / `traefik.key` | *(unset)* | Client certificate/key for that endpoint (mTLS). Both `url` and `cert` must be set for the cross-check to run at all. |
| `traefik.ca` | *(unset)* | CA bundle to verify the endpoint's server certificate. Unset, the **system trust store** applies — `ssl.create_default_context()` loads OpenSSL's default paths, so a corporate CA installed in `/etc/ssl/certs` *is* picked up, and `SSL_CERT_FILE`/`SSL_CERT_DIR` override them as usual. Set this only for a CA the system does not know; doing so replaces the system roots rather than adding to them. The HTTP library's own default never applies here — the cross-check requires `traefik.cert`, so the request always carries an explicitly built `SSLContext`. |
| `traefik.links` | `{}` | Table mapping an entrypoint **name** to the `http://` or `https://` base URL it is actually reached at, e.g. `login_example_de = "https://login.example.de"`. Independent of `traefik.url`/`cert`/`key`/`ca` above — it needs no connection to Traefik at all. See {doc}`Traefik wiring </explanation/traefik-wiring>` for why this has to be configured rather than derived. A value that is not a string, or does not start with `http://`/`https://`, is dropped; that entrypoint then simply has no links, the same as leaving it out. |

## Full example

```toml
# Fallback width for non-TTY (MOTD) rendering. Interactive logins auto-detect
# the real terminal width regardless of this value.
width = 200

[docker]
timeout = 1.5
description_label = "status.description"
show_image = true
infrastructure_stacks = ["postgresql", "kafka", "mongodb", "rustfs", "portainer", "traefik", "registry"]
infra_ui_services = ["kafbat-ui", "cloudbeaver", "mongo-express", "rustfs-console"]

[services]
critical = ["postgres", "kafka"]

[thresholds.memory]
warning = 75
critical = 90

[thresholds.swap]
warning = 1

[thresholds.filesystem]
warning = 80
critical = 90

[thresholds.load]
warning = 0.8   # per-CPU multiplier
critical = 1.0

[health]
budget = 5.0
enabled = ["postgres", "mongodb", "kafka", "glusterfs", "rustfs"]

[health.timeout]
postgres = 1.5
mongodb = 2.5
kafka = 4.0
glusterfs = 1.0
rustfs = 2.0
wireguard = 1.0
dns = 2.5

[[health.dns.expect]]
name = "login.example.net"
addresses = ["10.9.9.9"]

[traefik.links]
login_example_de = "https://login.example.de"
portal_dept_uni_example_de = "https://portal.dept.uni-example.de"

[resources]
process_sample = 0.3
top_processes = 5

[follow]
interval = 5.0          # sections without health
health_interval = 20.0  # sections including health
```
