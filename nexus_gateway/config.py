from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import yaml


@dataclass
class MeshCoreConfig:
    mode: str = "serial"
    serial_port: Optional[str] = None
    baudrate: Optional[int] = None
    host: Optional[str] = None
    port: Optional[int] = None
    # Connection-resilience knobs (apply to both serial and tcp modes).
    # auto_reconnect/max_reconnect_attempts are handed straight to the
    # meshcore_py ConnectionManager; command_timeout_sec bounds every
    # command call (get_uptime, send_channel_message, ...) so a dead
    # link doesn't hang a loop indefinitely.
    auto_reconnect: bool = True
    max_reconnect_attempts: int = 20
    command_timeout_sec: Optional[float] = 10.0


@dataclass
class MqttConfig:
    host: str
    port: int
    username: str
    password: str
    keepalive: int
    tls: bool
    uplink_topic: str
    downlink_topic: str
    heartbeat_topic: str
    status_topic: str


@dataclass
class RuntimeConfig:
    dedupe_ttl_sec: int
    heartbeat_interval_sec: int
    poll_interval_sec: int
    log_level: str
    beacon_interval_sec: int = 10800
    beacon_channel: int = 2
    beacon_text: str = ""
    advert_interval_sec: int = 3600
    advert_enabled: bool = False
    flood_advert_interval_sec: int = 10800
    flood_advert_enabled: bool = False
    default_scope_advert_enabled: bool = False
    default_scope_advert_interval_sec: int = 10800
    # Consecutive companion-health-check failures (get_uptime, or the
    # connection reporting itself as down) before the gateway forces a
    # full disconnect/reconnect cycle instead of waiting for systemd to
    # restart the whole process.
    watchdog_failure_threshold: int = 3


@dataclass
class GatewayConfig:
    gateway_id: str
    site_name: str
    region: str
    mesh_id: str
    radio_band: str
    channel_name: str
    channel_number: int
    channel_scope: str
    default_scope: str
    channel_secret: str
    path_hash_mode: int
    protocol_version: str
    meshcore: MeshCoreConfig
    mqtt: MqttConfig
    runtime: RuntimeConfig


def _build_meshcore_config(meshcore_data: dict) -> MeshCoreConfig:
    mode = str(meshcore_data.get("mode", "serial")).strip().lower()
    if mode not in ("serial", "tcp"):
        raise ValueError(
            f"invalid meshcore.mode '{mode}': expected 'serial' or 'tcp'"
        )

    auto_reconnect = bool(meshcore_data.get("auto_reconnect", True))
    max_reconnect_attempts = int(meshcore_data.get("max_reconnect_attempts", 20))
    if max_reconnect_attempts < 1:
        raise ValueError("meshcore.max_reconnect_attempts must be >= 1")
    raw_timeout = meshcore_data.get("command_timeout_sec", 10.0)
    command_timeout_sec = float(raw_timeout) if raw_timeout is not None else None

    if mode == "tcp":
        host = meshcore_data.get("host")
        port = meshcore_data.get("port")
        if not host:
            raise ValueError("meshcore.mode is 'tcp' but 'host' is not set")
        if not port:
            raise ValueError("meshcore.mode is 'tcp' but 'port' is not set")
        return MeshCoreConfig(
            mode=mode,
            host=str(host),
            port=int(port),
            auto_reconnect=auto_reconnect,
            max_reconnect_attempts=max_reconnect_attempts,
            command_timeout_sec=command_timeout_sec,
        )

    serial_port = meshcore_data.get("serial_port")
    if not serial_port:
        raise ValueError("meshcore.mode is 'serial' but 'serial_port' is not set")
    return MeshCoreConfig(
        mode=mode,
        serial_port=str(serial_port),
        baudrate=int(meshcore_data.get("baudrate", 115200)),
        auto_reconnect=auto_reconnect,
        max_reconnect_attempts=max_reconnect_attempts,
        command_timeout_sec=command_timeout_sec,
    )


def load_config(path: str | Path) -> GatewayConfig:
    data = yaml.safe_load(Path(path).read_text())
    meshcore_data = data.get("meshcore", data.get("meshcli", {}))
    channel_scope = str(data.get("channel_scope", "it-lom-mi"))
    return GatewayConfig(
        gateway_id=data["gateway_id"],
        site_name=data["site_name"],
        region=data["region"],
        mesh_id=data["mesh_id"],
        radio_band=str(data["radio_band"]),
        channel_name=data["channel_name"],
        channel_number=int(data["channel_number"]),
        channel_scope=channel_scope,
        default_scope=str(data.get("default_scope", channel_scope)),
        channel_secret=str(data.get("channel_secret", "")),
        path_hash_mode=int(data.get("path_hash_mode", 1)),
        protocol_version=str(data["protocol_version"]),
        meshcore=_build_meshcore_config(meshcore_data),
        mqtt=MqttConfig(**data["mqtt"]),
        runtime=RuntimeConfig(
            dedupe_ttl_sec=data["runtime"]["dedupe_ttl_sec"],
            heartbeat_interval_sec=data["runtime"]["heartbeat_interval_sec"],
            poll_interval_sec=data["runtime"]["poll_interval_sec"],
            log_level=data["runtime"]["log_level"],
            beacon_interval_sec=int(data["runtime"].get("beacon_interval_sec", 10800)),
            beacon_channel=int(data["runtime"].get("beacon_channel", 2)),
            beacon_text=str(data["runtime"].get("beacon_text", "")),
            advert_interval_sec=int(data["runtime"].get("advert_interval_sec", 3600)),
            advert_enabled=bool(data["runtime"].get("advert_enabled", False)),
            flood_advert_interval_sec=int(data["runtime"].get("flood_advert_interval_sec", 10800)),
            flood_advert_enabled=bool(data["runtime"].get("flood_advert_enabled", False)),
            default_scope_advert_enabled=bool(data["runtime"].get("default_scope_advert_enabled", False)),
            default_scope_advert_interval_sec=int(data["runtime"].get("default_scope_advert_interval_sec", 10800)),
            watchdog_failure_threshold=int(data["runtime"].get("watchdog_failure_threshold", 3)),
        ),
    )
