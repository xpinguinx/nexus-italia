from __future__ import annotations

import json
import logging
import ssl
from typing import Callable, Optional

import paho.mqtt.client as mqtt

from .config import MqttConfig

logger = logging.getLogger("nexus_gateway.mqtt_client")


class GatewayMqttClient:
    def __init__(
        self,
        config: MqttConfig,
        on_downlink: Callable[[dict], None],
    ) -> None:
        self.config = config
        self.on_downlink = on_downlink
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self.client.username_pw_set(config.username, config.password)
        if config.tls:
            self.client.tls_set(cert_reqs=ssl.CERT_REQUIRED)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_disconnect = self._on_disconnect

    def connect(self) -> None:
        self.client.connect(self.config.host, self.config.port, self.config.keepalive)
        self.client.loop_start()

    def disconnect(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()

    def publish_json(self, topic: str, payload: dict) -> None:
        self.client.publish(topic, json.dumps(payload, ensure_ascii=False), qos=1)

    def publish_text(self, topic: str, payload: str) -> None:
        self.client.publish(topic, payload, qos=1)

    def _on_connect(self, client: mqtt.Client, userdata: object, flags: object, reason_code: object, properties: object) -> None:
        logger.info("mqtt connected", extra={"extra": {"reason_code": str(reason_code)}})
        client.subscribe(self.config.downlink_topic, qos=1)

    def _on_disconnect(self, client: mqtt.Client, userdata: object, disconnect_flags: object, reason_code: object, properties: object) -> None:
        logger.warning("mqtt disconnected", extra={"extra": {"reason_code": str(reason_code)}})

    def _on_message(self, client: mqtt.Client, userdata: object, message: mqtt.MQTTMessage) -> None:
        try:
            payload = json.loads(message.payload.decode())
        except Exception:
            logger.exception("invalid downlink payload")
            return
        self.on_downlink(payload)
