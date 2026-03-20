from __future__ import annotations

import hashlib
import json
import logging
import subprocess
from datetime import datetime, timezone
from typing import Any, Dict, List

from .config import GatewayConfig

logger = logging.getLogger("nexus_gateway.meshcli")


class MeshCliAdapter:
    def __init__(self, config: GatewayConfig) -> None:
        self.config = config

    def _base_cmd(self) -> List[str]:
        return [
            self.config.meshcli.command,
            "-j",
            "-s",
            self.config.meshcli.serial_port,
            "-b",
            str(self.config.meshcli.baudrate),
        ]

    def _run(self, *args: str) -> str:
        cmd = self._base_cmd() + list(args)
        logger.debug("running meshcli command", extra={"extra": {"cmd": cmd}})
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=self.config.meshcli.timeout_sec,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"meshcli failed rc={completed.returncode}: {completed.stderr.strip() or completed.stdout.strip()}"
            )
        return completed.stdout.strip()

    def probe_channels(self) -> List[Dict[str, Any]]:
        output = self._run("get_channels")
        return json.loads(output or "[]")

    def sync_msgs(self) -> List[Dict[str, Any]]:
        output = self._run("sync_msgs")
        data = json.loads(output or "[]")
        if not isinstance(data, list):
            return []
        return data

    def send_channel_message(self, payload: str) -> None:
        self._run("chan", str(self.config.channel_number), payload)

    def normalize_messages(self, raw_messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        for item in raw_messages:
            chan_name = str(item.get("channel_name") or item.get("channel") or item.get("chan_name") or "")
            chan_idx = item.get("channel_idx", item.get("channel_number", item.get("chan")))
            payload = self._extract_payload(item)
            if not payload:
                continue
            if chan_name and chan_name != self.config.channel_name:
                continue
            if chan_idx is not None and int(chan_idx) != self.config.channel_number:
                continue
            sender = str(item.get("from") or item.get("sender") or item.get("sender_id") or "unknown")
            msg_id = str(item.get("msg_id") or item.get("id") or self._build_msg_id(sender, payload))
            normalized.append(
                {
                    "msg_id": msg_id,
                    "protocol_version": self.config.protocol_version,
                    "direction": "uplink",
                    "origin_gateway_id": self.config.gateway_id,
                    "origin_site_name": self.config.site_name,
                    "origin_region": self.config.region,
                    "origin_mesh_id": self.config.mesh_id,
                    "radio_band": self.config.radio_band,
                    "channel": self.config.channel_name,
                    "sender_mesh_node": sender,
                    "timestamp_utc": self._timestamp(item),
                    "payload_type": "text",
                    "payload": payload,
                    "payload_hash": hashlib.sha256(payload.encode()).hexdigest(),
                }
            )
        return normalized

    def _extract_payload(self, item: Dict[str, Any]) -> str:
        for key in ("payload", "msg", "message", "text", "body"):
            val = item.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        return ""

    def _timestamp(self, item: Dict[str, Any]) -> str:
        for key in ("timestamp_utc", "timestamp", "ts"):
            val = item.get(key)
            if isinstance(val, str) and val:
                return val
        return datetime.now(timezone.utc).isoformat()

    def _build_msg_id(self, sender: str, payload: str) -> str:
        bucket = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
        base = f"{self.config.gateway_id}|{sender}|{self.config.channel_number}|{payload}|{bucket}"
        return hashlib.sha256(base.encode()).hexdigest()
