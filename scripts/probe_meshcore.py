#!/usr/bin/env python3
"""Diagnostic helper used by install_gateway.sh / migrate_gateway.sh to read
the companion's channel table, and for manual troubleshooting.

The project no longer depends on meshcore-cli / the `meshcli` binary: the
gateway talks to the companion directly through the meshcore_py library
(see nexus_gateway/meshcore_adapter.py). This script covers the same manual
checks the old `meshcli` binary was used for, on that same library, working
for both serial and tcp companions:

  probe_meshcore.py --mode serial --serial-port /dev/ttyUSB0 --baudrate 115200
      (equivalente al vecchio: meshcli -j -s <porta> -b <baud> get_channels)

  probe_meshcore.py --mode tcp --host 192.168.1.50 --port 5000
      (stessa lettura canali, ma su companion raggiungibile via rete)

  probe_meshcore.py --mode serial --serial-port /dev/ttyUSB0 \\
      --send-text "test nexus" --channel 1
      (equivalente al vecchio: meshcli -j -s <porta> -b <baud> chan 1 "test nexus")

Not part of the running gateway service - install-time / troubleshooting
tool only.
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


def _jsonify(value):
    """Rende un valore compatibile con json.dumps.

    Il companion puo' restituire campi binari (es. il secret del canale
    come bytes grezzi): json.dumps non li accetta cosi' come sono, quindi
    li convertiamo in esadecimale, ricorsivamente dentro dict/list.
    """
    if isinstance(value, (bytes, bytearray)):
        return value.hex()
    if isinstance(value, dict):
        return {k: _jsonify(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonify(v) for v in value]
    return value


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
        channels.append(_jsonify(entry))
    return channels


async def _send_text(mc: MeshCore, channel: int, text: str) -> None:
    # Stessa chiamata usata da nexus_gateway/meshcore_adapter.py
    # (MeshCoreAdapter.send_channel_message) per il downlink MQTT->mesh.
    await mc.commands.send_chan_msg(chan=channel, msg=text)


async def main_async(args: argparse.Namespace) -> int:
    mc = await _connect(args)
    try:
        if args.send_text is not None:
            await _send_text(mc, args.channel, args.send_text)
            print(f"Messaggio inviato sul canale {args.channel}: {args.send_text!r}")
        else:
            channels = await _probe_channels(mc, args.max_channels)
            print(json.dumps(channels, ensure_ascii=False, indent=2, default=str))
    finally:
        try:
            await mc.disconnect()
        except Exception:
            pass
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
    parser.add_argument(
        "--send-text",
        help="Invece di leggere i canali, invia questo testo sul canale --channel (test radio)",
    )
    parser.add_argument(
        "--channel", type=int, help="Numero canale per --send-text (richiesto se --send-text e' usato)"
    )
    args = parser.parse_args(argv)
    if args.mode == "serial" and not args.serial_port:
        parser.error("--serial-port e' richiesto con --mode serial")
    if args.mode == "tcp" and (not args.host or not args.port):
        parser.error("--host e --port sono richiesti con --mode tcp")
    if args.send_text is not None and args.channel is None:
        parser.error("--channel e' richiesto insieme a --send-text")
    return args


def main() -> None:
    args = parse_args()
    try:
        rc = asyncio.run(main_async(args))
    except Exception as exc:
        print(f"Errore: {exc}", file=sys.stderr)
        sys.exit(1)
    sys.exit(rc)


if __name__ == "__main__":
    main()
