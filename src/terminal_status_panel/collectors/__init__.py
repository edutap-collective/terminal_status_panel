"""Collectors: measure the host, and never raise.

Every collector is time-boxed and exception-safe, because this code runs on
the login path. A collector that cannot measure something returns a value
saying so -- it does not guess, and it does not stop the shell.
"""
