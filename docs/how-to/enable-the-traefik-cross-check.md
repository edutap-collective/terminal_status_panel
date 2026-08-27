# Enable the Traefik live cross-check

The `[traefik]` config section (`url`, `cert`, `key`, `ca` — see
{doc}`Configuration reference </reference/configuration>`) is meant to close the
"as configured" gap: given Traefik's `/api/rawdata` endpoint and a client
certificate, the collector asks Traefik what it actually accepted and
records the answer per router.

**It is dormant on every app server today, and should stay unset.** Reaching
that endpoint needs a client certificate signed by the **webfe CA**, and the
Ansible role that provisions app servers currently issues only
**app-server TinyCA** certificates — for Traefik→service mTLS, a different
trust chain than the one the dashboard's own listener expects. Configuring
`traefik.url` without a certificate the dashboard accepts does not error: an
unreachable or rejected connection is treated the same as "not configured"
(see `fetch_accepted` in `collectors/traefik.py`) and the check is silently
skipped, so no test will surface the mistake. Leave the section unset until
the app servers have a certificate from the right CA.

When the cross-check does run, the tree shows its answer: a router Traefik
reported as *not* enabled is marked `💀 rejected by Traefik` on its own line
— the configuration is there, Traefik declined it. The accepted case adds
nothing: the tree already reads as configured-and-accepted, and a second
checkmark on every line would only be noise.

Nothing is marked unless Traefik was actually asked and actually answered
about that router. With `[traefik]` unset, unreachable, or answering in a
shape the parser cannot read (a router whose entry carries no `status` at
all), no marker appears — "we did not ask" and "Traefik said no" must never
look alike.
