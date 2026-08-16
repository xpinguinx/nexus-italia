from __future__ import annotations

import asyncio
import logging
import signal
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import __version__
from .config import GatewayConfig
from .dedupe import TTLCache
from .meshcore_adapter import MeshCoreAdapter
from .mqtt_client import GatewayMqttClient

logger = logging.getLogger("nexus_gateway.service")


class GatewayService:
    def __init__(self, config: GatewayConfig) -> None:
        self.config = config
        self.meshcore = MeshCoreAdapter(config)
        self.dedupe = TTLCache(config.runtime.dedupe_ttl_sec)
        self.stop_event = asyncio.Event()
        self.mqtt = GatewayMqttClient(config.mqtt, self._schedule_downlink)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._last_companion_uptime: int = 0
        self._health_check_failures: int = 0

    async def start(self) -> None:
        logger.info(
            "gateway service starting",
            extra={"extra": {"gateway_id": self.config.gateway_id}},
        )
        self._loop = asyncio.get_running_loop()
        self._install_signal_handlers()

        await self.meshcore.connect()
        await self.meshcore.sync_clock()
        await self._ensure_nexus_channel()
        await self._configure_scope()
        await self._configure_default_scope()
        await self.meshcore.set_path_hash_mode(self.config.path_hash_mode)

        self.mqtt.connect()
        self.publish_status("online")

        tasks = [
            asyncio.create_task(self._heartbeat_loop(), name="heartbeat"),
            asyncio.create_task(self._message_consumer_loop(), name="msg_consumer"),
            asyncio.create_task(self._companion_health_loop(), name="companion_health"),
        ]
        if self.config.runtime.beacon_text:
            tasks.append(asyncio.create_task(self._beacon_loop(), name="beacon"))
        if self.config.runtime.advert_enabled:
            tasks.append(asyncio.create_task(self._advert_loop(), name="advert"))
            logger.info(
                "advert 0hop enabled",
                extra={"extra": {"interval_sec": self.config.runtime.advert_interval_sec}},
            )
        if self.config.runtime.flood_advert_enabled:
            tasks.append(
                asyncio.create_task(self._flood_advert_loop(), name="flood_advert")
            )
            logger.info(
                "flood advert enabled",
                extra={"extra": {"interval_sec": self.config.runtime.flood_advert_interval_sec}},
            )
        if self.config.runtime.default_scope_advert_enabled:
            tasks.append(
                asyncio.create_task(self._default_scope_advert_loop(), name="default_scope_advert")
            )
            logger.info(
                "default scope flood advert enabled",
                extra={"extra": {
                    "scope": self.config.default_scope,
                    "interval_sec": self.config.runtime.default_scope_advert_interval_sec,
                }},
            )

        logger.info("gateway service started")
        await self.stop_event.wait()

        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

        self.publish_status("offline")
        self.mqtt.disconnect()
        await self.meshcore.disconnect()
        logger.info("gateway service stopped")

    def _install_signal_handlers(self) -> None:
        assert self._loop is not None
        try:
            self._loop.add_signal_handler(signal.SIGTERM, self._request_shutdown)
            self._loop.add_signal_handler(signal.SIGINT, self._request_shutdown)
        except NotImplementedError:
            signal.signal(signal.SIGTERM, self._signal_handler)
            signal.signal(signal.SIGINT, self._signal_handler)

    def _request_shutdown(self) -> None:
        logger.info("shutdown requested")
        self.stop_event.set()

    def _signal_handler(self, signum: int, frame: object) -> None:
        logger.info("shutdown requested", extra={"extra": {"signal": signum}})
        self.stop_event.set()

    async def _ensure_nexus_channel(self) -> None:
        if not self.config.channel_secret:
            return
        try:
            await self.meshcore.ensure_channel(
                self.config.channel_number,
                self.config.channel_name,
                self.config.channel_secret,
            )
        except Exception as exc:
            logger.exception(
                "failed to ensure nexus channel on companion",
                extra={"extra": {"error": str(exc)}},
            )

    async def _configure_scope(self) -> None:
        scope = self.config.channel_scope
        try:
            await self.meshcore.set_scope(scope)
            logger.info(
                "channel scope configured", extra={"extra": {"scope": scope}}
            )
        except Exception as exc:
            logger.exception(
                "failed to set channel scope",
                extra={"extra": {"error": str(exc), "scope": scope}},
            )

    async def _configure_default_scope(self) -> None:
        scope = self.config.default_scope
        try:
            await self.meshcore.set_default_scope(scope)
            logger.info(
                "default flood scope configured", extra={"extra": {"scope": scope}}
            )
        except Exception as exc:
            logger.exception(
                "failed to set default flood scope",
                extra={"extra": {"error": str(exc), "scope": scope}},
            )

    async def _wait_or_stop(self, seconds: float) -> bool:
        try:
            await asyncio.wait_for(self.stop_event.wait(), timeout=seconds)
            return True
        except asyncio.TimeoutError:
            return False

    async def _fetch_messages_or_stop(self) -> Optional[List[Dict[str, Any]]]:
        """Wait for uplink messages, but race the wait against stop_event so
        shutdown stays instant instead of being bounded by poll_interval_sec
        (which get_pending_messages(wait=True, ...) alone can't do, since it
        only wakes on a message arriving or its own timeout — it doesn't
        know about the service's stop_event). Returns None if shutdown was
        requested before any messages arrived."""
        fetch_task = asyncio.ensure_future(
            self.meshcore.get_pending_messages(
                wait=True, timeout=self.config.runtime.poll_interval_sec
            )
        )
        stop_task = asyncio.ensure_future(self.stop_event.wait())
        done, _ = await asyncio.wait(
            {fetch_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if fetch_task in done:
            stop_task.cancel()
            try:
                await stop_task
            except asyncio.CancelledError:
                pass
            return fetch_task.result()
        fetch_task.cancel()
        try:
            await fetch_task
        except (asyncio.CancelledError, Exception):
            pass
        return None

    async def _message_consumer_loop(self) -> None:
        # _fetch_messages_or_stop() blocks until the first uplink message
        # arrives (event-driven relay) or stop_event is set, with
        # poll_interval_sec as the upper bound before re-checking either —
        # so poll_interval_sec is now only a ceiling on relay latency and
        # shutdown responsiveness stays instant, not the steady-state delay
        # it used to be.
        while not self.stop_event.is_set():
            try:
                raw = await self._fetch_messages_or_stop()
            except Exception as exc:
                logger.exception(
                    "message consumer failed to fetch messages",
                    extra={"extra": {"error": str(exc)}},
                )
                await self._wait_or_stop(self.config.runtime.poll_interval_sec)
                continue

            if not raw:
                continue

            try:
                normalized = self.meshcore.normalize_messages(raw)
                for msg in normalized:
                    msg_id = msg["msg_id"]
                    if self.dedupe.seen(msg_id):
                        continue
                    self.dedupe.add(msg_id)
                    self.mqtt.publish_json(
                        self.config.mqtt.uplink_topic, msg
                    )
                    logger.info(
                        "uplink published",
                        extra={"extra": {
                            "msg_id": msg_id,
                            "channel": self.config.channel_name,
                        }},
                    )
            except Exception as exc:
                logger.exception(
                    "message consumer failed to process messages",
                    extra={"extra": {"error": str(exc)}},
                )

    async def _reapply_companion_config(self) -> None:
        """Re-run the companion init sequence (clock sync, nexus channel,
        scope, path-hash mode). Shared by the reboot-detected branch below
        and by the watchdog after a forced reconnect."""
        await self.meshcore.sync_clock()
        await self._ensure_nexus_channel()
        await self._configure_scope()
        await self._configure_default_scope()
        await self.meshcore.set_path_hash_mode(self.config.path_hash_mode)

    async def _companion_health_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                if not self.meshcore.is_connected:
                    # meshcore_py's own auto-reconnect either isn't enabled
                    # or has exhausted max_reconnect_attempts and given up;
                    # skip straight to counting this as a failure instead
                    # of issuing a command we know will fail/hang.
                    raise ConnectionError("companion link is down")
                uptime = await self.meshcore.get_uptime()
                if uptime < self._last_companion_uptime:
                    logger.warning(
                        "companion reboot detected, re-applying scope",
                        extra={"extra": {
                            "prev_uptime": self._last_companion_uptime,
                            "new_uptime": uptime,
                        }},
                    )
                    await self._reapply_companion_config()
                self._last_companion_uptime = uptime
                self._health_check_failures = 0
            except Exception as exc:
                self._health_check_failures += 1
                logger.warning(
                    "companion health check failed",
                    extra={"extra": {
                        "error": str(exc),
                        "consecutive_failures": self._health_check_failures,
                        "threshold": self.config.runtime.watchdog_failure_threshold,
                    }},
                )
                if self._health_check_failures >= self.config.runtime.watchdog_failure_threshold:
                    await self._force_reconnect()
            await self._wait_or_stop(self.config.runtime.heartbeat_interval_sec)

    async def _force_reconnect(self) -> None:
        """Watchdog action: called once the companion link has failed its
        health check watchdog_failure_threshold times in a row. Tears the
        connection down and rebuilds it, instead of relying solely on
        systemd's Restart=always (which loses in-flight state and takes
        longer, since it re-execs the whole process)."""
        logger.warning(
            "companion watchdog: forcing reconnect after repeated failures",
            extra={"extra": {"consecutive_failures": self._health_check_failures}},
        )
        try:
            await self.meshcore.reconnect()
            await self._reapply_companion_config()
            self._last_companion_uptime = 0
            self._health_check_failures = 0
            logger.info("companion watchdog: reconnect succeeded")
        except Exception as exc:
            # Leave _health_check_failures as-is: the next health-check
            # iteration will see is_connected == False, fail again, and
            # retry _force_reconnect on the following heartbeat tick.
            logger.exception(
                "companion watchdog: forced reconnect failed, will retry next cycle",
                extra={"extra": {"error": str(exc)}},
            )

    async def _heartbeat_loop(self) -> None:
        while not self.stop_event.is_set():
            self.publish_heartbeat()
            await self._wait_or_stop(self.config.runtime.heartbeat_interval_sec)

    async def _beacon_loop(self) -> None:
        await self._wait_or_stop(10)
        while not self.stop_event.is_set():
            try:
                await self.meshcore.send_beacon(
                    self.config.runtime.beacon_channel,
                    self.config.runtime.beacon_text,
                )
            except Exception as exc:
                logger.exception(
                    "beacon transmit failed",
                    extra={"extra": {"error": str(exc)}},
                )
            await self._wait_or_stop(self.config.runtime.beacon_interval_sec)

    async def _advert_loop(self) -> None:
        await self._wait_or_stop(15)
        while not self.stop_event.is_set():
            try:
                await self.meshcore.send_advert()
            except Exception as exc:
                logger.exception(
                    "advert 0hop failed",
                    extra={"extra": {"error": str(exc)}},
                )
            await self._wait_or_stop(self.config.runtime.advert_interval_sec)

    async def _flood_advert_loop(self) -> None:
        await self._wait_or_stop(20)
        while not self.stop_event.is_set():
            try:
                await self.meshcore.send_flood_advert()
            except Exception as exc:
                logger.exception(
                    "flood advert failed",
                    extra={"extra": {"error": str(exc)}},
                )
            await self._wait_or_stop(self.config.runtime.flood_advert_interval_sec)

    async def _default_scope_advert_loop(self) -> None:
        await self._wait_or_stop(25)
        while not self.stop_event.is_set():
            try:
                await self.meshcore.send_default_scope_flood_advert(self.config.default_scope)
            except Exception as exc:
                logger.exception(
                    "default scope flood advert failed",
                    extra={"extra": {"error": str(exc), "scope": self.config.default_scope}},
                )
            await self._wait_or_stop(self.config.runtime.default_scope_advert_interval_sec)

    def _schedule_downlink(self, payload: dict) -> None:
        if self._loop is not None and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(
                asyncio.ensure_future,
                self._handle_downlink(payload),
            )

    async def _handle_downlink(self, payload: dict) -> None:
        msg_id = str(payload.get("msg_id") or "")
        if msg_id and self.dedupe.seen(msg_id):
            logger.info(
                "downlink ignored duplicate",
                extra={"extra": {"msg_id": msg_id}},
            )
            return
        text = str(payload.get("payload") or "").strip()
        if not text:
            logger.warning("downlink ignored empty payload")
            return
        try:
            await self.meshcore.send_channel_message(text)
            if msg_id:
                self.dedupe.add(msg_id)
            logger.info(
                "downlink transmitted",
                extra={"extra": {
                    "msg_id": msg_id,
                    "channel_number": self.config.channel_number,
                }},
            )
        except Exception as exc:
            logger.exception(
                "downlink transmit failed",
                extra={"extra": {"error": str(exc), "msg_id": msg_id}},
            )

    def publish_heartbeat(self) -> None:
        meshcore_cfg = self.config.meshcore
        if meshcore_cfg.mode == "tcp":
            connection_target = f"{meshcore_cfg.host}:{meshcore_cfg.port}"
        else:
            connection_target = meshcore_cfg.serial_port
        payload = {
            "gateway_id": self.config.gateway_id,
            "site_name": self.config.site_name,
            "region": self.config.region,
            "radio_band": self.config.radio_band,
            "status": "online",
            # Kept as "serial_port" for backward compatibility with existing
            # MQTT consumers/dashboards; holds "host:port" when mode is tcp.
            "serial_port": connection_target,
            "connection_mode": meshcore_cfg.mode,
            "last_seen_utc": datetime.now(timezone.utc).isoformat(),
            "protocol_version": self.config.protocol_version,
            "software_version": __version__,
        }
        self.mqtt.publish_json(self.config.mqtt.heartbeat_topic, payload)

    def publish_status(self, status: str) -> None:
        payload = {
            "gateway_id": self.config.gateway_id,
            "status": status,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        }
        self.mqtt.publish_json(self.config.mqtt.status_topic, payload)
