from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from meshcore import MeshCore
from meshcore.events import EventType

from .config import GatewayConfig

logger = logging.getLogger("nexus_gateway.meshcore")


class MeshCoreAdapter:
    def __init__(self, config: GatewayConfig) -> None:
        self.config = config
        self._mc: Optional[MeshCore] = None
        self._msg_queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()
        self._subscription = None

    @property
    def is_connected(self) -> bool:
        return self._mc is not None and self._mc.is_connected

    async def connect(self) -> None:
        mode = self.config.meshcore.mode
        # auto_reconnect/max_reconnect_attempts let meshcore_py's own
        # ConnectionManager ride out short link drops (WiFi hiccups, a
        # flaky serial-to-network bridge, ...) without tearing the whole
        # adapter down. command_timeout_sec bounds every command call so a
        # dead link can't hang a loop indefinitely instead of failing fast
        # and letting the companion-health watchdog react.
        common_kwargs = dict(
            auto_reconnect=self.config.meshcore.auto_reconnect,
            max_reconnect_attempts=self.config.meshcore.max_reconnect_attempts,
            default_timeout=self.config.meshcore.command_timeout_sec,
        )
        if mode == "tcp":
            target = f"{self.config.meshcore.host}:{self.config.meshcore.port}"
            logger.info(
                "connecting to companion",
                extra={"extra": {
                    "mode": "tcp",
                    "host": self.config.meshcore.host,
                    "port": self.config.meshcore.port,
                    "auto_reconnect": common_kwargs["auto_reconnect"],
                    "max_reconnect_attempts": common_kwargs["max_reconnect_attempts"],
                    "command_timeout_sec": common_kwargs["default_timeout"],
                }},
            )
            self._mc = await MeshCore.create_tcp(
                self.config.meshcore.host,
                self.config.meshcore.port,
                **common_kwargs,
            )
        else:
            target = self.config.meshcore.serial_port
            logger.info(
                "connecting to companion",
                extra={"extra": {
                    "mode": "serial",
                    "port": self.config.meshcore.serial_port,
                    "baudrate": self.config.meshcore.baudrate,
                    "auto_reconnect": common_kwargs["auto_reconnect"],
                    "max_reconnect_attempts": common_kwargs["max_reconnect_attempts"],
                    "command_timeout_sec": common_kwargs["default_timeout"],
                }},
            )
            self._mc = await MeshCore.create_serial(
                self.config.meshcore.serial_port,
                baudrate=self.config.meshcore.baudrate,
                **common_kwargs,
            )
        if self._mc is None:
            raise ConnectionError(
                f"failed to connect to companion ({mode}) on {target}"
            )
        self._subscription = self._mc.subscribe(
            EventType.CHANNEL_MSG_RECV, self._on_channel_message
        )
        await self._mc.start_auto_message_fetching()
        logger.info("companion connected, auto-fetch started")

    async def disconnect(self) -> None:
        if self._mc is not None:
            if self._subscription is not None:
                self._subscription.unsubscribe()
                self._subscription = None
            try:
                await self._mc.stop_auto_message_fetching()
            except Exception:
                pass
            await self._mc.disconnect()
            self._mc = None
            logger.info("companion disconnected")

    async def reconnect(self) -> None:
        """Force a full disconnect/reconnect cycle.

        Used by the gateway's companion-health watchdog when the link is
        down and meshcore_py's own auto-reconnect either isn't enabled or
        has exhausted max_reconnect_attempts and given up. Re-raises on
        failure so the caller can back off and retry on the next cycle.
        """
        logger.warning("forcing full reconnect to companion")
        try:
            await self.disconnect()
        except Exception:
            logger.exception("error while disconnecting during forced reconnect")
        await self.connect()

    async def _on_channel_message(self, event: Any) -> None:
        raw = event.payload if hasattr(event, "payload") else {}
        logger.debug(
            "raw channel message received",
            extra={"extra": {"raw_payload": raw, "raw_type": type(raw).__name__}},
        )
        if isinstance(raw, str):
            raw = {"text": raw}
        # Filter: only relay messages from the configured Nexus channel
        # Use explicit None checks — channel_idx 0 (Public) is falsy but valid
        msg_chan = raw.get("channel_idx")
        if msg_chan is None:
            msg_chan = raw.get("channel")
        if msg_chan is None:
            msg_chan = raw.get("chan")
        if msg_chan is None or int(msg_chan) != self.config.channel_number:
            logger.debug(
                "ignoring message from non-nexus channel",
                extra={"extra": {
                    "msg_channel": msg_chan,
                    "nexus_channel": self.config.channel_number,
                }},
            )
            return
        await self._msg_queue.put(raw)

    async def get_pending_messages(
        self, wait: bool = False, timeout: Optional[float] = None
    ) -> List[Dict[str, Any]]:
        """Drain whatever is currently queued.

        By default this is non-blocking (original behaviour). With
        wait=True and an empty queue, blocks for up to `timeout` seconds
        for the first message to arrive (event-driven relay instead of
        fixed-interval polling), then drains anything else queued
        alongside it without waiting further. Returns [] if the timeout
        elapses with nothing received.
        """
        messages: List[Dict[str, Any]] = []
        if wait and self._msg_queue.empty():
            try:
                if timeout is not None:
                    first = await asyncio.wait_for(self._msg_queue.get(), timeout=timeout)
                else:
                    first = await self._msg_queue.get()
                messages.append(first)
            except asyncio.TimeoutError:
                return messages
        while not self._msg_queue.empty():
            try:
                messages.append(self._msg_queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return messages

    async def ensure_channel(
        self, channel_idx: int, name: str, secret_hex: str
    ) -> None:
        """Check that the Nexus channel exists on the companion; create it if not."""
        assert self._mc is not None
        try:
            result = await self._mc.commands.get_channel(channel_idx=channel_idx)
            info = result.payload if hasattr(result, "payload") else {}
            existing_name = ""
            if isinstance(info, dict):
                existing_name = str(
                    info.get("name") or info.get("channel_name") or ""
                ).strip().rstrip("\x00")
            if existing_name and existing_name.upper() == name.upper():
                logger.info(
                    "nexus channel already present on companion",
                    extra={"extra": {
                        "channel_idx": channel_idx,
                        "name": existing_name,
                    }},
                )
                return
        except Exception as exc:
            logger.warning(
                "could not read channel from companion, will attempt creation",
                extra={"extra": {"channel_idx": channel_idx, "error": str(exc)}},
            )

        secret_bytes = bytes.fromhex(secret_hex)
        await self._mc.commands.set_channel(channel_idx, name, secret_bytes)
        logger.info(
            "nexus channel created on companion",
            extra={"extra": {"channel_idx": channel_idx, "name": name}},
        )

    async def send_channel_message(self, text: str) -> None:
        assert self._mc is not None
        await self._mc.commands.send_chan_msg(
            chan=self.config.channel_number, msg=text
        )

    async def send_beacon(self, channel: int, text: str) -> None:
        assert self._mc is not None
        await self._mc.commands.send_chan_msg(chan=channel, msg=text)
        logger.info(
            "beacon transmitted",
            extra={"extra": {"text": text, "channel": channel}},
        )

    async def send_advert(self) -> None:
        assert self._mc is not None
        await self._mc.commands.send_advert(flood=False)
        logger.info("advert 0hop transmitted")

    async def send_flood_advert(self) -> None:
        assert self._mc is not None
        await self._mc.commands.send_advert(flood=True)
        logger.info("flood advert transmitted")

    async def send_default_scope_flood_advert(self, scope: str) -> None:
        assert self._mc is not None
        await self._mc.commands.set_default_flood_scope(scope)
        await self._mc.commands.send_advert(flood=True)
        logger.info("default scope flood advert transmitted", extra={"extra": {"scope": scope}})

    async def get_uptime(self) -> int:
        assert self._mc is not None
        result = await self._mc.commands.get_stats_core()
        data = result.payload if hasattr(result, "payload") else {}
        if isinstance(data, dict):
            return int(data.get("uptime_secs", data.get("uptime", 0)))
        return 0

    async def sync_clock(self) -> None:
        assert self._mc is not None
        import time
        await self._mc.commands.set_time(int(time.time()))
        logger.info("companion clock synced")

    async def set_scope(self, scope: str) -> None:
        assert self._mc is not None
        await self._mc.commands.set_flood_scope(scope)
        logger.info("channel scope set", extra={"extra": {"scope": scope}})

    async def set_default_scope(self, scope: str) -> None:
        assert self._mc is not None
        await self._mc.commands.set_default_flood_scope(scope)
        logger.info("default flood scope set", extra={"extra": {"scope": scope}})

    async def set_path_hash_mode(self, mode: int = 1) -> None:
        assert self._mc is not None
        await self._mc.commands.set_path_hash_mode(mode)
        logger.info("path hash mode set", extra={"extra": {"mode": mode}})

    def normalize_messages(
        self, raw_messages: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        for item in raw_messages:
            payload = self._extract_payload(item)
            if not payload:
                continue
            sender = str(
                item.get("from")
                or item.get("sender")
                or item.get("pubkey_prefix")
                or item.get("sender_id")
                or "unknown"
            )
            msg_id = str(
                item.get("msg_id")
                or item.get("id")
                or self._build_msg_id(sender, payload)
            )
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
        for key in ("text", "payload", "msg", "message", "body"):
            val = item.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        return ""

    def _timestamp(self, item: Dict[str, Any]) -> str:
        for key in ("timestamp_utc", "timestamp", "ts"):
            val = item.get(key)
            if isinstance(val, str) and val:
                return val
            if isinstance(val, (int, float)) and val > 0:
                return datetime.fromtimestamp(val, tz=timezone.utc).isoformat()
        return datetime.now(timezone.utc).isoformat()

    def _build_msg_id(self, sender: str, payload: str) -> str:
        bucket = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
        base = f"{self.config.gateway_id}|{sender}|{self.config.channel_number}|{payload}|{bucket}"
        return hashlib.sha256(base.encode()).hexdigest()
