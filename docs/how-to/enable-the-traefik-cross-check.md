# Enable the Traefik live cross-check

The `[traefik]` config section (`url`, `cert`, `key`, `ca` — see
{doc}`Configuration reference </reference/configuration>`) is meant to close the
"as configured" gap: given Traefik's `/api/rawdata` endpoint and a client
certificate, the collector asks Traefik what it actually accepted and
records the answer per router.

**It works only with a client certificate the dashboard accepts.** Where
Traefik's dashboard router requires a client certificate, the deployment has
to issue the panel one from the CA that router trusts. A certificate from
another chain — one issued for Traefik→service mTLS, say — does not do.
Configuring `traefik.url` without a certificate the dashboard accepts does
not error: an unreachable or rejected connection is treated the same as "not
configured" (see `fetch_accepted` in `collectors/traefik.py`) and the check
is silently skipped, so no test will surface the mistake. Leave the section
unset until the host has a certificate from the right CA.

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
