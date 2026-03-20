from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import yaml


@dataclass
class MeshCliConfig:
    command: str
    serial_port: str
    baudrate: int
    timeout_sec: int
    mode: str = "serial"


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


@dataclass
class GatewayConfig:
    gateway_id: str
    site_name: str
    region: str
    mesh_id: str
    radio_band: str
    channel_name: str
    channel_number: int
    protocol_version: str
    meshcli: MeshCliConfig
    mqtt: MqttConfig
    runtime: RuntimeConfig


def load_config(path: str | Path) -> GatewayConfig:
    data = yaml.safe_load(Path(path).read_text())
    return GatewayConfig(
        gateway_id=data["gateway_id"],
        site_name=data["site_name"],
        region=data["region"],
        mesh_id=data["mesh_id"],
        radio_band=str(data["radio_band"]),
        channel_name=data["channel_name"],
        channel_number=int(data["channel_number"]),
        protocol_version=str(data["protocol_version"]),
        meshcli=MeshCliConfig(**data["meshcli"]),
        mqtt=MqttConfig(**data["mqtt"]),
        runtime=RuntimeConfig(**data["runtime"]),
    )
