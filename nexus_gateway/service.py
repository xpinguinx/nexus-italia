from __future__ import annotations

import logging
import signal
import threading
import time
from datetime import datetime, timezone

from .config import GatewayConfig
from .dedupe import TTLCache
from .meshcli_adapter import MeshCliAdapter
from .mqtt_client import GatewayMqttClient

logger = logging.getLogger("nexus_gateway.service")


class GatewayService:
    def __init__(self, config: GatewayConfig) -> None:
        self.config = config
        self.meshcli = MeshCliAdapter(config)
        self.dedupe = TTLCache(config.runtime.dedupe_ttl_sec)
        self.stop_event = threading.Event()
        self.mqtt = GatewayMqttClient(config.mqtt, self.handle_downlink)
        self._heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)

    def start(self) -> None:
        logger.info("gateway service started", extra={"extra": {"gateway_id": self.config.gateway_id}})
        self.mqtt.connect()
        self.publish_status("online")
        self._heartbeat_thread.start()
        self._poll_thread.start()
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)
        while not self.stop_event.is_set():
            time.sleep(0.5)
        self.publish_status("offline")
        self.mqtt.disconnect()

    def _signal_handler(self, signum: int, frame: object) -> None:
        logger.info("shutdown requested", extra={"extra": {"signal": signum}})
        self.stop_event.set()

    def _heartbeat_loop(self) -> None:
        while not self.stop_event.is_set():
            self.publish_heartbeat()
            self.stop_event.wait(self.config.runtime.heartbeat_interval_sec)

    def _poll_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                raw = self.meshcli.sync_msgs()
                normalized = self.meshcli.normalize_messages(raw)
                for msg in normalized:
                    msg_id = msg["msg_id"]
                    if self.dedupe.seen(msg_id):
                        continue
                    self.dedupe.add(msg_id)
                    self.mqtt.publish_json(self.config.mqtt.uplink_topic, msg)
                    logger.info("uplink published", extra={"extra": {"msg_id": msg_id, "channel": self.config.channel_name}})
            except Exception as exc:
                logger.exception("mesh poll failed", extra={"extra": {"error": str(exc)}})
            self.stop_event.wait(self.config.runtime.poll_interval_sec)

    def handle_downlink(self, payload: dict) -> None:
        msg_id = str(payload.get("msg_id") or "")
        if msg_id and self.dedupe.seen(msg_id):
            logger.info("downlink ignored duplicate", extra={"extra": {"msg_id": msg_id}})
            return
        text = str(payload.get("payload") or "").strip()
        if not text:
            logger.warning("downlink ignored empty payload")
            return
        try:
            self.meshcli.send_channel_message(text)
            if msg_id:
                self.dedupe.add(msg_id)
            logger.info("downlink transmitted", extra={"extra": {"msg_id": msg_id, "channel_number": self.config.channel_number}})
        except Exception as exc:
            logger.exception("downlink transmit failed", extra={"extra": {"error": str(exc), "msg_id": msg_id}})

    def publish_heartbeat(self) -> None:
        payload = {
            "gateway_id": self.config.gateway_id,
            "site_name": self.config.site_name,
            "region": self.config.region,
            "radio_band": self.config.radio_band,
            "status": "online",
            "serial_port": self.config.meshcli.serial_port,
            "last_seen_utc": datetime.now(timezone.utc).isoformat(),
            "software_version": self.config.protocol_version,
        }
        self.mqtt.publish_json(self.config.mqtt.heartbeat_topic, payload)

    def publish_status(self, status: str) -> None:
        payload = {
            "gateway_id": self.config.gateway_id,
            "status": status,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        }
        self.mqtt.publish_json(self.config.mqtt.status_topic, payload)
