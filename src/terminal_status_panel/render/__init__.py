"""Renderers: turn collected data into Rich renderables.

Nothing here talks to a system. A renderer receives what a collector
measured and decides how to say it -- which is why the verdict rules are
testable without a Docker socket.
"""
