# Install the panel

```bash
pip install terminal-status-panel     # from PyPI, once published
# or, from a checkout:
pip install .
```

This installs six panel commands (`status-full`, `status-server`,
`status-docker`, `status-health`, `status-trouble`, `status-traefik`), the
`status-collect` collector, and an `install-panel` helper to wire the panel
into a login shell. The collector is optional; see
{doc}`install-the-collector`.
