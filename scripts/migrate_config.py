#!/usr/bin/env python3
"""Migra un config.yaml di una installazione precedente (sezione `meshcli:`,
nessun campo di resilienza/scope) al nuovo schema (sezione `meshcore:`,
supporto TCP, resilienza connessione, watchdog).

Usato da migrate_gateway.sh, ma eseguibile anche a mano per revisionare il
risultato prima di applicarlo:

  .venv/bin/python scripts/migrate_config.py config.yaml.bak config.yaml.new

Principi seguiti:
- ogni valore GIA' presente nel file vecchio viene preservato cosi' com'e'
  (compresi eventuali campi gia' nel nuovo schema, per rendere lo script
  idempotente su un config.yaml gia' migrato);
- i campi NUOVI mancanti vengono aggiunti con default sicuri e documentati,
  mai inventando segreti o abilitando funzioni ad ampio impatto (advert /
  flood advert restano disabilitati, channel_secret resta vuoto) a meno che
  non fossero gia' impostati nel file di partenza;
- `meshcli.command` e `meshcli.timeout_sec` (residui del vecchio design a
  CLI, mai letti dal codice) vengono scartati - pero' se timeout_sec era
  stato personalizzato dall'operatore, il suo valore viene comunque
  riportato nel nuovo command_timeout_sec, che ha un significato analogo.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

DEFAULT_CHANNEL_SCOPE = "it-lo"
DEFAULT_DEFAULT_SCOPE_FALLBACK = "it"
DEFAULT_PATH_HASH_MODE = 1
DEFAULT_AUTO_RECONNECT = True
DEFAULT_MAX_RECONNECT_ATTEMPTS = 20
DEFAULT_COMMAND_TIMEOUT_SEC = 10
DEFAULT_WATCHDOG_THRESHOLD = 3
DEFAULT_ADVERT_INTERVAL_SEC = 3600
DEFAULT_FLOOD_ADVERT_INTERVAL_SEC = 10800


def _yaml_str(value) -> str:
    """Quota una stringa per l'output YAML solo se necessario (numeri,
    booleani e stringhe "semplici" restano senza virgolette, come nello
    stile gia' usato da install_gateway.sh)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    return f"\"{text}\""


def build_migrated_config(old: dict) -> tuple[str, list[str]]:
    """Ritorna (testo_yaml_nuovo, elenco_note) a partire dal dict del vecchio
    config.yaml gia' parsato."""
    notes: list[str] = []

    def added(field: str, value) -> None:
        notes.append(f"aggiunto {field} = {value!r} (non presente nel file originale)")

    def carried(field: str, source_field: str, value) -> None:
        notes.append(f"riportato {field} = {value!r} (da {source_field} del vecchio schema)")

    # --- campi identita' top-level, sempre gia' presenti in ogni versione ---
    required_top = ["gateway_id", "site_name", "region", "mesh_id", "radio_band",
                     "channel_name", "channel_number", "protocol_version"]
    missing_required = [f for f in required_top if f not in old]
    if missing_required:
        raise ValueError(
            "il file non sembra un config.yaml valido del gateway nexus-italia: "
            f"campi obbligatori mancanti: {', '.join(missing_required)}"
        )

    channel_scope = old.get("channel_scope")
    if channel_scope is None:
        channel_scope = DEFAULT_CHANNEL_SCOPE
        added("channel_scope", channel_scope)

    default_scope = old.get("default_scope")
    if default_scope is None:
        default_scope = channel_scope or DEFAULT_DEFAULT_SCOPE_FALLBACK
        added("default_scope", default_scope)

    channel_secret = old.get("channel_secret")
    if channel_secret is None:
        channel_secret = ""
        added("channel_secret", "(vuoto - il canale Nexus esiste gia' sul companion, "
                                 "non viene ricreato/verificato finche' non imposti un secret)")

    path_hash_mode = old.get("path_hash_mode")
    if path_hash_mode is None:
        path_hash_mode = DEFAULT_PATH_HASH_MODE
        added("path_hash_mode", path_hash_mode)

    # --- sezione connessione: meshcore: se gia' presente, altrimenti meshcli: ---
    conn = old.get("meshcore")
    source_section = "meshcore"
    if conn is None:
        conn = old.get("meshcli", {})
        source_section = "meshcli"
        if conn:
            notes.append("sezione 'meshcli:' rinominata in 'meshcore:'")

    mode = str(conn.get("mode", "serial")).strip().lower()
    if mode not in ("serial", "tcp"):
        mode = "serial"

    auto_reconnect = conn.get("auto_reconnect")
    if auto_reconnect is None:
        auto_reconnect = DEFAULT_AUTO_RECONNECT
        added("meshcore.auto_reconnect", auto_reconnect)

    max_reconnect_attempts = conn.get("max_reconnect_attempts")
    if max_reconnect_attempts is None:
        max_reconnect_attempts = DEFAULT_MAX_RECONNECT_ATTEMPTS
        added("meshcore.max_reconnect_attempts", max_reconnect_attempts)

    command_timeout_sec = conn.get("command_timeout_sec")
    if command_timeout_sec is None:
        legacy_timeout = conn.get("timeout_sec")
        if legacy_timeout is not None:
            command_timeout_sec = legacy_timeout
            carried("meshcore.command_timeout_sec", f"{source_section}.timeout_sec", command_timeout_sec)
        else:
            command_timeout_sec = DEFAULT_COMMAND_TIMEOUT_SEC
            added("meshcore.command_timeout_sec", command_timeout_sec)

    meshcore_lines = [f"meshcore:", f"  mode: {mode}"]
    if mode == "tcp":
        host = conn.get("host", "")
        port = conn.get("port", "")
        if not host or not port:
            raise ValueError(
                "meshcore.mode e' 'tcp' ma host/port non sono presenti nel file di partenza: "
                "controlla manualmente il config.yaml originale."
            )
        meshcore_lines.append(f"  host: {host}")
        meshcore_lines.append(f"  port: {port}")
    else:
        serial_port = conn.get("serial_port")
        if not serial_port:
            raise ValueError(
                "meshcore.mode e' 'serial' ma serial_port non e' presente nel file di partenza: "
                "controlla manualmente il config.yaml originale."
            )
        baudrate = conn.get("baudrate", 115200)
        meshcore_lines.append(f"  serial_port: {serial_port}")
        meshcore_lines.append(f"  baudrate: {baudrate}")
    meshcore_lines.append(f"  auto_reconnect: {_yaml_str(auto_reconnect)}")
    meshcore_lines.append(f"  max_reconnect_attempts: {_yaml_str(max_reconnect_attempts)}")
    meshcore_lines.append(f"  command_timeout_sec: {_yaml_str(command_timeout_sec)}")

    # --- mqtt: schema invariato fin dalla prima versione, copiato cosi' com'e' ---
    if "mqtt" not in old:
        raise ValueError("il file non ha una sezione 'mqtt:' - non e' un config.yaml valido.")
    mqtt = old["mqtt"]
    mqtt_field_order = ["host", "port", "username", "password", "keepalive", "tls",
                         "uplink_topic", "downlink_topic", "heartbeat_topic", "status_topic"]
    missing_mqtt = [f for f in mqtt_field_order if f not in mqtt]
    if missing_mqtt:
        raise ValueError(f"sezione 'mqtt:' incompleta, mancano: {', '.join(missing_mqtt)}")
    # host/username/topic/etc restano non quotati nel nuovo file, come lo
    # erano gia' nel vecchio (stile coerente con quanto genera install_gateway.sh).
    mqtt_lines = ["mqtt:"]
    for f in mqtt_field_order:
        value = mqtt[f]
        rendered = _yaml_str(value) if isinstance(value, bool) else value
        mqtt_lines.append(f"  {f}: {rendered}")

    # --- runtime: preserva tutto il presente, aggiunge i nuovi campi mancanti ---
    if "runtime" not in old:
        raise ValueError("il file non ha una sezione 'runtime:' - non e' un config.yaml valido.")
    runtime = old["runtime"]
    runtime_required = ["dedupe_ttl_sec", "heartbeat_interval_sec", "poll_interval_sec", "log_level"]
    missing_runtime = [f for f in runtime_required if f not in runtime]
    if missing_runtime:
        raise ValueError(f"sezione 'runtime:' incompleta, mancano: {', '.join(missing_runtime)}")

    runtime_lines = ["runtime:"]
    for f in runtime_required:
        runtime_lines.append(f"  {f}: {runtime[f]}")

    watchdog_threshold = runtime.get("watchdog_failure_threshold")
    if watchdog_threshold is None:
        watchdog_threshold = DEFAULT_WATCHDOG_THRESHOLD
        added("runtime.watchdog_failure_threshold", watchdog_threshold)
    runtime_lines.append(f"  watchdog_failure_threshold: {watchdog_threshold}")

    # beacon: aggiunto SOLO se gia' presente nel vecchio file (non ha senso
    # inventare un testo di beacon per un gateway esistente).
    if "beacon_text" in runtime and runtime.get("beacon_text"):
        runtime_lines.append(f"  beacon_interval_sec: {runtime.get('beacon_interval_sec', 10800)}")
        runtime_lines.append(f"  beacon_channel: {runtime.get('beacon_channel', old.get('channel_number'))}")
        runtime_lines.append(f"  beacon_text: \"{runtime['beacon_text']}\"")

    advert_enabled = runtime.get("advert_enabled")
    if advert_enabled is None:
        advert_enabled = False
        added("runtime.advert_enabled", advert_enabled)
    advert_interval = runtime.get("advert_interval_sec", DEFAULT_ADVERT_INTERVAL_SEC)
    flood_advert_enabled = runtime.get("flood_advert_enabled")
    if flood_advert_enabled is None:
        flood_advert_enabled = False
        added("runtime.flood_advert_enabled", flood_advert_enabled)
    flood_advert_interval = runtime.get("flood_advert_interval_sec", DEFAULT_FLOOD_ADVERT_INTERVAL_SEC)
    runtime_lines.append(f"  advert_enabled: {_yaml_str(advert_enabled)}")
    runtime_lines.append(f"  advert_interval_sec: {advert_interval}")
    runtime_lines.append(f"  flood_advert_enabled: {_yaml_str(flood_advert_enabled)}")
    runtime_lines.append(f"  flood_advert_interval_sec: {flood_advert_interval}")

    # default_scope_advert_* esiste nello schema ma e' un'aggiunta avanzata
    # rara: la portiamo avanti solo se gia' presente nel vecchio file.
    if "default_scope_advert_enabled" in runtime:
        runtime_lines.append(f"  default_scope_advert_enabled: {_yaml_str(runtime['default_scope_advert_enabled'])}")
    if "default_scope_advert_interval_sec" in runtime:
        runtime_lines.append(f"  default_scope_advert_interval_sec: {runtime['default_scope_advert_interval_sec']}")

    lines = [
        f"gateway_id: {old['gateway_id']}",
        f"site_name: \"{old['site_name']}\"",
        f"region: {old['region']}",
        f"mesh_id: {old['mesh_id']}",
        f"radio_band: \"{old['radio_band']}\"",
        f"channel_name: {old['channel_name']}",
        f"channel_number: {old['channel_number']}",
        f"channel_scope: \"{channel_scope}\"",
        f"default_scope: \"{default_scope}\"",
        f"channel_secret: \"{channel_secret}\"",
        f"path_hash_mode: {path_hash_mode}",
        f"protocol_version: \"{old['protocol_version']}\"",
        "",
    ] + meshcore_lines + [""] + mqtt_lines + [""] + runtime_lines

    return "\n".join(lines) + "\n", notes


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(f"uso: {argv[0]} <vecchio_config.yaml> <nuovo_config.yaml>", file=sys.stderr)
        return 2
    old_path, new_path = Path(argv[1]), Path(argv[2])
    old_data = yaml.safe_load(old_path.read_text())
    try:
        text, notes = build_migrated_config(old_data)
    except ValueError as exc:
        print(f"Migrazione non riuscita: {exc}", file=sys.stderr)
        return 1
    new_path.write_text(text)
    for note in notes:
        print(f"- {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
