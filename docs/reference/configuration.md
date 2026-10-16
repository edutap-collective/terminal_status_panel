# Configuration

Zero configuration is required. Settings are read from
`/etc/terminal-status-panel/config.toml` (override with `--config PATH`). A missing
or unreadable file falls back to the built-in defaults — it never raises.

## Configuration reference

| Key | Default | Meaning |
|-----|---------|---------|
| `width` | `80` | Fallback render width when no TTY is available (see width resolution above). |
| `sections` | *(unset)* | Which sections a bare `status-full` renders, from `server`, `health`, `trouble`, `docker`, `traefik`, in the order given. Unset (or `[]`), the default applies: all five, or without `docker` and `traefik` on a host with no Docker endpoint. A name outside that set is dropped and reported by `--debug`. A named command (`status-health`, …) and `--sections` ignore this key. |
| `docker.timeout` | `1.5` | Seconds to wait for the Docker socket before giving up (also bounds the `apt` update check). Keeps a hung/absent daemon from delaying login. |
| `docker.description_label` | `"status.description"` | Docker **service label** read as the per-service description column. The key `lmu.service.description` is still read as a fallback. |
| `docker.group_label` | `"status.group"` | Docker **service label** naming the row a service belongs in. Services of one stack sharing a value render as one row, whatever their names. Where it is absent the name heuristic decides instead — see {doc}`Grouping services into one row </explanation/docker-panel>`. Read for presence, not truthiness: a service setting it to `""` groups with no one. |
| `docker.df_timeout` | `4.0` | Seconds to wait for `/system/df`, the Docker disk reading — deliberately larger than `docker.timeout` and spent on a **separate client**. The call was measured at 510 ms against a daemon holding 47 images and 185 volumes, and it grows with the object count. Overrunning it costs the one line, never the whole DOCKER INFOS section. See {doc}`Docker\'s own disk footprint </explanation/docker-panel>`. |
| `docker.show_image` | `true` | Whether the DOCKER INFOS rows carry an **Image** column right of the description (see {doc}`The image column </explanation/docker-panel>`). It is the column that answers "which version is deployed here", and the one that costs the description its width on a narrow terminal — `false` removes it. |
| `docker.legend` | `true` | Whether DOCKER INFOS opens with a legend naming every glyph the section can show (see {doc}`Icon vocabulary </reference/icon-vocabulary>`). All of them, always, wrapped between entries rather than inside one. Where the clusters were not probed — `status-docker` alone collects no health — the `⬜` entry says so and names `status-health`. `false` removes the line. |
| `resources.ignore_mountpoints` | platform-dependent | Mountpoint prefixes hidden from the filesystem table. Defaults to `["/System/Volumes/", "/Library/Developer/CoreSimulator/"]` on macOS and to `[]` elsewhere. An explicitly empty list hides nothing rather than falling back to the default. |
| `resources.process_sample` | `0.3` | Seconds to sample process CPU usage over for the TOP CPU row (see {doc}`Top processes </explanation/top-processes>`). `0` or less disables the CPU ranking; TOP RAM is unaffected. |
| `resources.top_processes` | `5` | Rows per process table in the TOP CPU / TOP RAM row (see {doc}`Top processes </explanation/top-processes>`). `--processes N` on the command line wins over this. A value that cannot be read as a whole number falls back to `5`; a negative value means `0`. `0` removes the whole row, and with it the `process_sample` sampling wait — a different switch from `process_sample`, which only removes the CPU ranking and leaves TOP RAM in place. |
| `docker.infrastructure_stacks` | `["postgresql", "postgres", "kafka", "mongodb", "rustfs", "portainer", "traefik", "registry", "minio", "redis", "valkey", "mariadb", "mysql", "elasticsearch", "bugsink", "swarm-cronjob", "swarm_cronjob"]` | Case-insensitive substrings. A **stack** (or Compose project) whose name matches goes into that origin's **Infrastructure** table; every other stack goes into **Service**. An entry with no stack at all is never classified this way — it has no project to be filed under, so `docker run -d redis` lands in **Standalone containers** like any other stackless entry, however infrastructural its name. |
| `docker.infra_ui_services` | `["kafbat-ui", "kafka-ui", "kafdrop", "cloudbeaver", "pgadmin", "adminer", "mongo-express", "mongo-gui", "rustfs-console", "rustfs-ui", "s3-browser", "s3browser", "s3manager", "redisinsight", "redis-commander", "dozzle", "kibana"]` | Case-insensitive substrings matched against the stack name **and** the service name. Matching services leave their own stack and are collected as sub-rows of the pseudo stack **`infra-uis`**, shown first in the **Infrastructure** block. On a name matching both lists, this one wins. A sidecar pulled in only because its *stack* name matched is labelled `stack/service` so it stays attributable once detached. Also excluded from every cluster health match — an admin UI sharing its cluster's stack name is never probed. |
| `services.critical` | `[]` | Service names flagged as critical (parsed and available on the data model; not visually emphasised in the current matrix view). |
| `thresholds.memory.warning` / `.critical` | `75` / `90` | RAM usage % thresholds (yellow / red). |
| `thresholds.swap.warning` | platform-dependent | Swap usage % above which SWAP turns yellow. Defaults to `80` on macOS, which allocates swap continuously by design, and to `1` elsewhere. An explicit value overrides both. |
| `thresholds.filesystem.warning` / `.critical` | `80` / `90` | Filesystem usage % thresholds. |
| `thresholds.load.warning` / `.critical` | `0.8` / `1.0` | Load-average thresholds as a **per-CPU multiplier** (compared against `load1 / cpu_count`). |
| `thresholds.psi.warning` / `.critical` | `10` / `25` | Pressure stall information, in percent of the last 60 seconds in which something waited (`avg60`). Colours the PSI row under the load average (`cpu 4 % · 9 %  (10 s · 60 s)`: the 10 s and the 60 s figure). The row is omitted where the kernel has no `/proc/pressure` (macOS, FreeBSD, kernels without PSI). `memory` and `io` show only when above zero. |
| `health.budget` | `8.0` | Total wall-clock budget in seconds for all health checks. Every check runs concurrently as its own task, so this bounds the login delay — it is not the sum of the individual timeouts. |
| `health.timeout.*` | postgres `1.5`, mongodb `6.0`, kafka `4.0`, glusterfs `1.0`, rustfs `2.0`, systemd `1.0`, wireguard `1.0`, dns `2.5` | Deadline for one check. Each cluster kind, the peer check and the DNS check are separate tasks; a task that overruns its value is reported as `… <name>: time budget exceeded` while every other check keeps its result. Values above `health.budget` have no effect — the budget always wins. See {doc}`How the timeouts are enforced </explanation/cluster-health>`. |
| `health.enabled` | all eight kinds | Which kinds to probe: the cluster kinds `postgres`, `mongodb`, `kafka`, `glusterfs`, `rustfs` and the host kinds `systemd`, `vrrp`, `apache`. The host kinds need no Docker. `vrrp` and `apache` read the collector's state file and are *not applicable* (`n/a here`) without keepalived data or without `[health.apache] url`, respectively; without a state file, `vrrp` is an error line only where `/run/keepalived.pid` exists. Taking `vrrp` or `apache` out also stops the collector from gathering it. |
| `health.apache.url` | *(unset)* | mod_status URL, without `?auto` (the collector adds it), e.g. `http://localhost/server-status`. Must be an `http://` or `https://` URL with a host and no query or fragment; anything else switches the kind off and is reported by `--debug`. Unset, the `apache` kind is *not applicable* and the collector does not fetch. |
| `health.apache.requests_warning` / `.requests_critical` | *(unset)* | Requests per second at which the Apache line turns yellow / red. The request rate is coloured only when **both** are set; with one of them unset it carries no colour. |
| `health.apache.slots_warning` / `.slots_critical` | `80` / `95` | Percent of scoreboard slots in use at which the Apache line turns yellow / red. |
| `health.vrrp.lead_warning` / `.lead_critical` | `5` / `0` | Applied only where a BACKUP's *lead* is known (its effective priority minus the priority the master advertises). A lead of `lead_critical` or more is red; a lead within `lead_warning` below the master is yellow. A FAULT, STOP or DELETED instance is red regardless. |
| `health.systemd.ignore` | `[]` | Unit names that may be failed without turning the `systemd` line red. Read like `health.<kind>.match`. |
| `trouble.host_events` | all five kinds | Which host events TROUBLE reports, and which the collector counts: `oom_kill`, `vrrp_state`, `apache_slots_full`, `unit_failed`, `apache_reload`. `[]` reports none. An unknown name is dropped and reported by `--debug`. |
| `health.dns.expect` | `[]` | Array of `{name, addresses}`. `addresses` is optional; without it the name only has to resolve at all. |
| `health.postgres.match` / `health.mongodb.match` / `health.kafka.match` / `health.rustfs.match` | `["_pg-"]` / `["mongodb"]` / `["kafka_kafka-"]` / `["rustfs_rustfs"]` | Case-insensitive substrings of a container (and Swarm service) name that identify a member of that kind. Replaces the default rather than extending it. A bare string is one pattern; `[]` means no member of that kind on this host (it renders `n/a here`); an empty string is dropped and reported by `--debug`, because it would match every container. Names containing an entry of `docker.infra_ui_services` never count as members. |
| `health.postgres.mode` | `"pg_auto_failover"` | `"pg_auto_failover"` runs `pg_autoctl show state` and reports every member. `"standalone"` runs `pg_isready` and reports one server: whether it accepts connections, nothing about replication. |
| `health.kafka.command_config` | `"/client.properties"` | Client config passed to `kafka-metadata-quorum.sh` as `--command-config`. `""` omits the option — upstream `apache/kafka` images carry no such file. |
| `health.rustfs.scheme` | `"https"` | Scheme of the local-instance endpoint (`localhost:9000`), used when `RUSTFS_VOLUMES` holds a path rather than URLs. URL-form endpoints keep their own scheme. |
| `follow.interval` | `5.0` | Refresh interval in seconds for `--follow` when the `health` section is **not** among those requested (see {doc}`Follow mode </explanation/follow-mode>`). |
| `follow.health_interval` | `20.0` | Refresh interval in seconds for `--follow` when the `health` section **is** among those requested. |
| `traefik.match` | `["traefik_traefik"]` | Case-insensitive substrings identifying the Traefik workload: a Swarm service name, or — when no service matches — a plain container's name. Read like `health.<kind>.match`. An empty list (`[]`) means Traefik's wiring is not read on this host at all — no Docker call and no API cross-check is made for it: the TRAEFIK WIRING section renders one dim line saying so and nothing else. |
| `traefik.url` | *(unset)* | URL of Traefik's `/api/rawdata` endpoint for the optional live cross-check. It works only where the deployment issues the panel a client certificate that Traefik's dashboard router accepts; without one, leave it unset — see {doc}`Enable the Traefik live cross-check </how-to/enable-the-traefik-cross-check>`. |
| `traefik.cert` / `traefik.key` | *(unset)* | Client certificate/key for that endpoint (mTLS). Both `url` and `cert` must be set for the cross-check to run at all. |
| `traefik.ca` | *(unset)* | CA bundle to verify the endpoint's server certificate. Unset, the **system trust store** applies — `ssl.create_default_context()` loads OpenSSL's default paths, so a corporate CA installed in `/etc/ssl/certs` *is* picked up, and `SSL_CERT_FILE`/`SSL_CERT_DIR` override them as usual. Set this only for a CA the system does not know; doing so replaces the system roots rather than adding to them. The HTTP library's own default never applies here — the cross-check requires `traefik.cert`, so the request always carries an explicitly built `SSLContext`. |
| `managed.by` | *(unset)* | The name of the tool that configures this machine, e.g. `Ansible`. Setting it renders the **MANAGED** block under UPDATES; leaving it unset renders nothing at all, which is the default for every installation. Not tied to any one tool — Puppet, Salt and Chef are the same case. |
| `managed.repository` | *(unset)* | Where that configuration lives, as a full `http://` or `https://` URL. Rendered as its last path segment and linked to in full, because a repository URL is some sixty characters and the column is the narrow one. Validated like `traefik.links`: a value that is not a usable web address is dropped and reported by `--debug`, rather than shown as a link that goes somewhere wrong. Without `managed.by` it renders nothing. |
| `managed.detail` | *(unset)* | One short line under the tool name. What it says is a local decision — `no local changes`, `changes via merge request only`, a ticket queue. |
| `traefik.links` | `{}` | Table mapping an entrypoint **name** to the `http://` or `https://` base URL it is actually reached at, e.g. `login_example_de = "https://login.example.de"`. Independent of `traefik.url`/`cert`/`key`/`ca` above — it needs no connection to Traefik at all. See {doc}`Traefik wiring </explanation/traefik-wiring>` for why this has to be configured rather than derived. A value that is not a string, or does not start with `http://`/`https://`, is dropped; that entrypoint then simply has no links, the same as leaving it out. |

(state-file-schema)=
## The host state file, schema 1

Not configuration: the file `status-collect` writes and the panel reads, at
`/run/terminal-status-panel/host.json` (mode `0644`, replaced atomically). It
holds numbers, states, counts and timestamps, and never log text, paths or
client addresses. See {doc}`About host health </explanation/host-health>`.

| Field | Type | Meaning |
|-------|------|---------|
| `schema` | integer | Always `1`. Another value makes the panel report "host state unavailable (unknown schema)". |
| `written_at` | number | Unix time of the run. The panel's *state N old* is measured from it; older than 120 s is stale. |
| `collector` | string | Version of the package that wrote the file. |
| `vrrp` | object or `null` | `null` when disabled or unreadable. |
| `vrrp.source` | string | `keepalived-json`. |
| `vrrp.instances[]` | list | One entry per VRRP instance. |
| `vrrp.instances[].name` | string | Instance name (`iname`). |
| `vrrp.instances[].state` | string | `MASTER`, `BACKUP`, `FAULT`, `INIT`, `STOP`, `DELETED` or `UNKNOWN`. |
| `vrrp.instances[].base_priority`, `.effective_priority` | integer or `null` | The configured and the current priority. |
| `vrrp.instances[].master_priority` | integer or `null` | The priority the master advertises. Meaningful on a BACKUP only. |
| `apache` | object or `null` | `null` when `[health.apache] url` is unset or the read failed. |
| `apache.window_s` | number | Seconds between this run and the previous reading; `0` without one. |
| `apache.requests_per_s` | number or `null` | Rate over that window; `null` without a previous reading or after a restart. |
| `apache.slots_busy`, `.workers_busy` | number or `null` | Shares between 0 and 1. |
| `apache.slots_finishing` | integer or `null` | Slots held by gracefully finishing processes (`G`). |
| `updates` | object or `null` | `null` where no simulation could run. |
| `updates.checked_at` | number | Unix time of the last successful simulation (at most hourly). |
| `updates.available`, `.security` | integer | Counts from the simulated upgrade. |
| `updates.source` | string | `apt-simulate`. |
| `events` | object | Per enabled event kind: `{"count": integer, "last": number or null}`. Empty if none is enabled or the journal could not be read. |
| `events_window_s` | integer | The window the counts cover: `43200` (12 h). |
| `errors` | object | Per source (`vrrp`, `apache`, `updates`, `events`) `null` or a short code: `not-installed`, `no-permission`, `timeout`, `parse`, `unreachable`, `apt-failed`, `cleanup` (the keepalived copy could not be deleted), `internal`. |

Keys the panel does not know are ignored.

## Full example

```toml
# Fallback width for non-TTY (MOTD) rendering. Interactive logins auto-detect
# the real terminal width regardless of this value.
width = 200

# What a bare status-full renders; unset, the host decides.
sections = ["server", "health", "trouble"]

[docker]
timeout = 1.5
description_label = "status.description"
show_image = true
legend = true
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

[thresholds.psi]
warning = 10    # percent of the last 60 s
critical = 25

[health]
budget = 5.0
enabled = ["postgres", "mongodb", "kafka", "glusterfs", "rustfs", "systemd", "vrrp", "apache"]

[health.timeout]
postgres = 1.5
mongodb = 2.5
kafka = 4.0
glusterfs = 1.0
rustfs = 2.0
wireguard = 1.0
dns = 2.5

[health.apache]
url = "http://localhost/server-status"
requests_warning = 400
requests_critical = 800
slots_warning = 80
slots_critical = 95

[health.vrrp]
lead_warning = 5
lead_critical = 0

[health.systemd]
ignore = ["example-optional.service"]

[trouble]
host_events = ["oom_kill", "vrrp_state", "apache_slots_full", "unit_failed", "apache_reload"]

[[health.dns.expect]]
name = "login.example.net"
addresses = ["10.9.9.9"]

# A host running plain upstream images, one of each:
[health.postgres]
match = ["myapp_postgres"]
mode = "standalone"

[health.kafka]
match = ["myapp_kafka"]
command_config = ""

[health.rustfs]
match = ["myapp_rustfs"]
scheme = "http"

[traefik]
match = ["myapp_traefik"]

[managed]
by = "Ansible"
repository = "https://git.example.net/ops/servers"
detail = "no local changes"

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
