#!/usr/bin/env python3
"""Small readiness helper for the WorkBuddy loopback CC Switch proxy.

``ensure_ccs_ready(upstream)`` returns a non-sensitive dict describing the
proxy: ``ready``, ``started``, ``port``, ``pid``, ``owned`` and ``owned_pid``.
It never reads or writes credentials, never touches HOME/CODEX_HOME, and only
ever stops a cc-switch process that this function started itself.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import math
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.parse import urlsplit

MAX_POLL_SECONDS = 30.0
CONNECT_TIMEOUT = 0.9
POLL_INTERVAL = 0.1


class CcsReadyError(RuntimeError):
    """Raised when CCS cannot be made ready; messages never contain credentials."""


def _loopback_port(upstream: object) -> tuple[str, int]:
    """Validate a plain loopback HTTP upstream and return (host, port)."""
    if not isinstance(upstream, str) or not upstream or upstream != upstream.strip():
        raise CcsReadyError("upstream must be a plain loopback HTTP URL")
    try:
        parts = urlsplit(upstream)
        port = parts.port
    except ValueError:
        raise CcsReadyError("upstream has an invalid port") from None
    if parts.scheme.lower() != "http":
        raise CcsReadyError("upstream must use plain HTTP")
    if "@" in parts.netloc:
        raise CcsReadyError("upstream must not contain credentials")
    if parts.query or parts.fragment:
        raise CcsReadyError("upstream must not contain a query or fragment")
    host = parts.hostname
    if not host:
        raise CcsReadyError("upstream must name a loopback host")
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host.lower() == "localhost"
    if not loopback:
        raise CcsReadyError("upstream host must be loopback")
    if port is None or not 1 <= port <= 65535:
        raise CcsReadyError("upstream must include a valid port")
    return host, port


def _port_open(host: str, port: int) -> bool:
    """Return True when a TCP connection to host:port succeeds."""
    try:
        with socket.create_connection((host, port), timeout=CONNECT_TIMEOUT):
            return True
    except OSError:
        return False


def _cc_switch_pids() -> list[int]:
    """Return PIDs of running cc-switch processes (mocked in tests)."""
    pids = []
    try:
        entries = list(Path("/proc").iterdir())
    except OSError:
        return pids
    for entry in entries:
        if not entry.name.isdigit():
            continue
        names = []
        try:
            names.append(os.readlink(entry / "exe"))
        except OSError:
            pass
        try:
            names.append((entry / "comm").read_text(encoding="utf-8", errors="replace").strip())
        except OSError:
            pass
        try:
            raw = (entry / "cmdline").read_bytes().split(b"\0", 1)[0]
            names.append(raw.decode("utf-8", errors="replace"))
        except OSError:
            pass
        for name in names:
            base = Path(name).name.lower()
            if base == "cc-switch" or base.startswith("cc-switch"):
                pids.append(int(entry.name))
                break
    return sorted(pids)


def _terminate_owned(proc: subprocess.Popen) -> None:
    """Stop only the process handle this function created."""
    try:
        proc.terminate()
        proc.wait(timeout=3)
        return
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        proc.kill()
        proc.wait(timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        pass


def ensure_ccs_ready(upstream: str, executable: Path = Path("/usr/bin/cc-switch"),
                     timeout: float = 30.0) -> dict:
    """Make the loopback cc-switch proxy reachable, or raise CcsReadyError.

    Already reachable: return immediately without starting anything. Closed with
    a cc-switch process already running: refuse, so no second profile instance
    is created. Otherwise start the executable and poll until the proxy is
    reachable. On timeout only the process started here is terminated.
    """
    host, port = _loopback_port(upstream)
    if _port_open(host, port):
        return {"ready": True, "started": False, "port": port, "pid": None,
                "owned": False, "owned_pid": None}
    try:
        budget = float(timeout)
    except (TypeError, ValueError):
        raise CcsReadyError("timeout must be a positive number") from None
    if not math.isfinite(budget) or budget <= 0:
        raise CcsReadyError("timeout must be a positive number")
    budget = min(budget, MAX_POLL_SECONDS)
    existing = _cc_switch_pids()
    if existing:
        raise CcsReadyError(
            f"cc-switch is already running (pid {existing[0]}); refusing to start a second instance")
    exe = Path(executable)
    if not (exe.is_file() and os.access(exe, os.X_OK)):
        raise CcsReadyError(f"cc-switch executable is missing or not executable: {exe}")
    try:
        proc = subprocess.Popen([str(exe)], stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                start_new_session=True)
    except OSError as exc:
        raise CcsReadyError(f"could not start cc-switch: {exc.strerror or 'exec failed'}") from None
    deadline = time.monotonic() + budget
    try:
        while True:
            if _port_open(host, port):
                return {"ready": True, "started": True, "port": port, "pid": proc.pid,
                        "owned": True, "owned_pid": proc.pid}
            if proc.poll() is not None:
                raise CcsReadyError("cc-switch exited before the proxy became ready")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CcsReadyError("timed out waiting for the cc-switch proxy to become ready")
            time.sleep(min(POLL_INTERVAL, remaining))
    except BaseException:
        _terminate_owned(proc)
        raise


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; --check only probes and never starts cc-switch."""
    parser = argparse.ArgumentParser(description="Ensure the loopback cc-switch proxy is ready")
    parser.add_argument("--upstream", required=True)
    parser.add_argument("--executable", type=Path, default=Path("/usr/bin/cc-switch"))
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--check", action="store_true", help="probe only; never start cc-switch")
    args = parser.parse_args(argv)
    try:
        if args.check:
            host, port = _loopback_port(args.upstream)
            if not _port_open(host, port):
                raise CcsReadyError("cc-switch proxy is not reachable")
            result = {"ready": True, "started": False, "port": port, "pid": None,
                      "owned": False, "owned_pid": None}
        else:
            result = ensure_ccs_ready(args.upstream, executable=args.executable,
                                      timeout=args.timeout)
    except CcsReadyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
