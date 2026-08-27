# Icon vocabulary

The section's core idea: it never claims to know something it did not
measure.

| Icon | Meaning |
|------|---------|
| ✅ | measured healthy |
| ⚠️ | warning (e.g. a lagging PostgreSQL replica, a diverging DNS entry) |
| 💀 | measured broken |
| ⏰ | a scheduled job, resting between successful runs |
| `⬜` | not observable / not attempted — see below for where it appears |
| `…` | the check ran out of the shared time budget |
| `✗` | the check itself failed (a command errored, a connection was refused) |
| `n/a here` | not applicable — this node runs no member of the service |

`…` and `✗` mean different things and must not be conflated: a budget
timeout says nothing about the service's health, only that the panel gave up
waiting for it; a failed check (`✗`) is a statement about the service, or
about the tool used to ask it.

Every icon in the table above occupies **two terminal cells**, and that is a
requirement rather than a coincidence: a column mixing a one-cell glyph with a
two-cell one steps left and right down the block. Until 0.10 the not-observable
marker was a middle dot (`·`), one cell against `✅`'s two, and every cluster
member list was ragged because of it. The same character still appears in the
panel as a *separator* — `active · manager · 5 nodes`, and the follow-mode
status line — which is a different use and unrelated to this vocabulary.

The empty square (`⬜`) means the panel did not measure that member, and since
0.10 that is the exception rather than the rule for MongoDB. `db.hello()` on
one node reports the set's membership but state only for the primary and for
the node answering — so the check now asks every member the same question
directly. `hello` is answered before authentication, which is what makes that
possible without credentials; `replSetGetStatus` would answer it all in one
round trip, including replication lag, but replies `Command replSetGetStatus
requires authentication`.

A member still renders `⬜` when the check ran out of its deadline before
reaching it. That is the degraded path and it is deliberately no worse than
the old display: whatever was reached is reported, the rest stays blank, and
an unearned ✅ is never invented for a member nobody asked. A member that *was*
asked and could not be reached is a different thing and reads as such —
`unreachable`, measured, not blank.

Every other check in this section (PostgreSQL's `pg_autoctl` rows, Kafka's
quorum voters, GlusterFS peers/bricks) reports ✅ or 💀 for each member it
lists, because those commands report per-member state too. A *service* whose own
quorum was never established shows no icon at all next to its name, just a
dim "quorum not reported" note — so "not observable" (the dot) and "never
asked" never look the same. That note appears whenever the panel has no basis
for a quorum verdict:

- the probe errored before parsing anything;
- the command succeeded but its output was not recognisable (an empty
  `pg_autoctl` table, a Kafka status in an unexpected format after an
  upgrade, a GlusterFS answer with neither peers nor volumes) — an
  unrecognised answer is *not* a measurement, and rendering 💀 for one would
  raise a red alarm for a perfectly healthy cluster;
- `gluster peer status` reports zero other peers, i.e. a single-node volume,
  which peer status alone cannot establish a quorum for;
- RustFS's `RUSTFS_VOLUMES` could not be read, so the endpoint list is a
  guess — the line then also reads `(endpoint list unknown)` next to the
  live count, because "1/1 live" against a guessed endpoint says nothing
  about a five-node cluster.

All three blocks can additionally read `not checked (…)` instead of a false
clean result: the CLUSTERS block shows `not checked (no Docker client)` when
there is no Docker socket to probe from (or every cluster kind is disabled in
config), the PEERS block shows `not checked (no peer list available)` when
the check produced no result *and* there were no peer names to check in
the first place — i.e. neither the `wg`/TCP probe answered nor a Swarm node
list gave it anything to ask about — and the DNS block shows `not checked
(DNS check did not run)` when the section has no collected data at all (the
DNS check itself always runs, so within a real run this line cannot appear;
it guards the case where the whole section is missing). All exist for
the same reason — an empty list from a check that never ran would otherwise
look identical to "checked, found nothing," which is a false clean bill of
health. Those lines are also prefixed with the same empty square (`⬜`) as the
MongoDB member case above: a block that never ran carries exactly that claim
— not observed, nothing more said.

Where a probe deliberately narrows what it looks at, it says so in the same
place: `(+N more volumes)` when a GlusterFS host serves more than the one
volume reported, and `(+N more containers)` when a node runs more than one
container of the same kind (a `pg16` → `pg18` migration, say) and only the
first was probed.
