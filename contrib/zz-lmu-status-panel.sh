# /etc/profile.d/zz-lmu-status-panel.sh
#
# Show the lmu.terminal_status_panel dashboard on interactive login shells.
# Because a login shell's stdout is a real terminal, the tool auto-detects and
# uses the FULL current terminal width (no fixed width needed).
#
# Install:
#   sudo install -m 0644 contrib/zz-lmu-status-panel.sh /etc/profile.d/
#
# If the command lives in a virtualenv, replace it with the absolute path,
# e.g. /opt/lmu/venv/bin/lmu-status-panel.
#
# Use EITHER this profile.d snippet OR the update-motd.d hook, not both.

case $- in *i*) ;; *) return ;; esac  # interactive shells only
command -v lmu-status-panel >/dev/null 2>&1 && lmu-status-panel
