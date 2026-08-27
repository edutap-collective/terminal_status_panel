# terminal-status-panel

A colourful server status panel for a login shell. It reads the machine it runs
on — system, resources, Docker and Swarm, clustered services, Traefik's wiring —
and renders one dashboard sized to the terminal it is printed into.

It is a diagnostic tool, so it never fails a login: the command always exits 0,
a collector that cannot answer degrades to a placeholder, and a check that runs
out of its time budget says so rather than guessing.

```{toctree}
:caption: Tutorials
:maxdepth: 1

tutorials/first-run
```

```{toctree}
:caption: How-to guides
:maxdepth: 1

how-to/install
how-to/run-it-at-login
how-to/diagnose-an-empty-panel
how-to/enable-the-traefik-cross-check
how-to/regenerate-the-logos
how-to/develop
how-to/release
```

```{toctree}
:caption: Reference
:maxdepth: 1

reference/commands
reference/configuration
reference/icon-vocabulary
reference/stability
```

```{toctree}
:caption: Explanation
:maxdepth: 1

explanation/cluster-health
explanation/traefik-wiring
explanation/docker-panel
explanation/the-trouble-block
explanation/top-processes
explanation/follow-mode
explanation/render-width
explanation/platform-behaviour
```

## Where to start

If you have never run it, start with {doc}`the first run </tutorials/first-run>`.

If it is already installed and showing you something you do not understand,
{doc}`the icon vocabulary </reference/icon-vocabulary>` is the shortest route,
and {doc}`diagnosing an empty panel </how-to/diagnose-an-empty-panel>` is the
one for when it is showing you nothing at all.
