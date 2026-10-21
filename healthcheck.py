"""
Container health check: succeeds when the FastAGI port accepts connections.

Reads host/port the same way tellows_agi.py does (environment first, then
config.yaml). Exits 0 when healthy, 1 otherwise.
"""
import os
import socket
import sys

host = os.environ.get("HOST")
port = os.environ.get("PORT")

if not host or not port:
    try:
        import yaml
        with open("config.yaml", "r") as stream:
            file_config = yaml.safe_load(stream) or {}
        host = host or file_config.get("host")
        port = port or file_config.get("port")
    except Exception:  # pylint: disable=broad-except
        # Missing/broken config: fall back to the defaults below.
        pass

# A wildcard listen address is reachable via loopback inside the container.
if not host or host in ("0.0.0.0", "::"):
    host = "127.0.0.1"

try:
    socket.create_connection((host, int(port or 4573)), 3).close()
except (OSError, ValueError) as exc:
    print("FastAGI not reachable on %s:%s: %s" % (host, port, exc))
    sys.exit(1)
