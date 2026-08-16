#!/usr/bin/env python3
"""Diagnostic helper used by install_gateway.sh to read the companion's
channel table during installation and for manual troubleshooting.

The project no longer depends on meshcore-cli / the `meshcli` binary: the
gateway talks to the companion directly through the meshcore_py library
(see nexus_gateway/meshcore_adapter.py). This script is the equivalent of
the old `meshcli -j -s <port> -b <baud> get_channels` call, but built on
that same library so it works for both serial and tcp companions.

Not part of the running gateway service - install-time / troubleshooting
tool only. Usage:

  probe_meshcore.py --mode serial --serial-port /dev/ttyUSB0 --baudrate 115200
  probe_meshcore.py --mode tcp --host 192.168.1.50 --port 5000
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from meshcore import MeshCore


async def _connect(args: argparse.Namespace) -> MeshCore:
    if args.mode == "tcp":
        mc = await MeshCore.create_tcp(
            args.host, args.port, default_timeout=args.timeout
        )
    else:
        mc = await MeshCore.create_serial(
            args.serial_port, baudrate=args.baudrate, default_timeout=args.timeout
        )
    if mc is None:
        target = f"{args.host}:{args.port}" if args.mode == "tcp" else args.serial_port
        raise ConnectionError(f"impossibile connettersi al companion MeshCore ({target})")
    return mc


async def _probe_channels(mc: MeshCore, max_channels: int) -> list:
    channels = []
    for idx in range(max_channels):
        try:
            result = await mc.commands.get_channel(channel_idx=idx)
        except Exception:
            # Companion has no more channel slots to report, or doesn't
            # support the command at all - either way, stop probing.
            break
        payload = result.payload if hasattr(result, "payload") else None
        if not payload:
            continue
        entry = dict(payload) if isinstance(payload, dict) else {"raw": payload}
        entry["channel_idx"] = idx
        channels.append(entry)
    return channels


async def main_async(args: argparse.Namespace) -> int:
    mc = await _connect(args)
    try:
        channels = await _probe_channels(mc, args.max_channels)
    finally:
        try:
            await mc.disconnect()
        except Exception:
            pass
    print(json.dumps(channels, ensure_ascii=False, indent=2))
    return 0


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Legge la tabella canali del companion MeshCore via meshcore_py"
    )
    parser.add_argument("--mode", choices=["serial", "tcp"], required=True)
    parser.add_argument("--serial-port", help="Porta seriale (richiesto con --mode serial)")
    parser.add_argument("--baudrate", type=int, default=115200)
    parser.add_argument("--host", help="Host/IP del companion (richiesto con --mode tcp)")
    parser.add_argument("--port", type=int, help="Porta TCP del companion (richiesto con --mode tcp)")
    parser.add_argument("--timeout", type=float, default=10.0, help="Timeout comandi (secondi)")
    parser.add_argument("--max-channels", type=int, default=8)
    args = parser.parse_args(argv)
    if args.mode == "serial" and not args.serial_port:
        parser.error("--serial-port e' richiesto con --mode serial")
    if args.mode == "tcp" and (not args.host or not args.port):
        parser.error("--host e --port sono richiesti con --mode tcp")
    return args


def main() -> None:
    args = parse_args()
    try:
        rc = asyncio.run(main_async(args))
    except Exception as exc:
        print(f"Errore durante la lettura canali: {exc}", file=sys.stderr)
        sys.exit(1)
    sys.exit(rc)


if __name__ == "__main__":
    main()
