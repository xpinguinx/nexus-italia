#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/nexus-gateway"
SERVICE_FILE="/etc/systemd/system/nexus-gateway.service"

# Connessione al Companion (seriale o TCP)
DEFAULT_CONN_MODE="serial"
DEFAULT_BAUD="115200"
DEFAULT_TCP_PORT="5000"

# Identita' canale Nexus
DEFAULT_CHANNEL_SCOPE="it-lo"
DEFAULT_DEFAULT_SCOPE="it"
DEFAULT_CHANNEL_SECRET="a45768ab48e203498edbc11b35cdfbd7"
DEFAULT_PATH_HASH_MODE="1"

# Resilienza connessione (vedi config.example.yaml per i dettagli)
DEFAULT_AUTO_RECONNECT="true"
DEFAULT_MAX_RECONNECT_ATTEMPTS="20"
DEFAULT_CMD_TIMEOUT="10"
DEFAULT_WATCHDOG_THRESHOLD="3"

# Runtime
DEFAULT_POLL="5"
DEFAULT_HEARTBEAT="30"
DEFAULT_DEDUPE="180"

# Beacon / advert: restano disabilitati di default (advert/flood_advert
# impattano tutta la mesh, non solo il canale Nexus), regolabili a mano
# dopo l'installazione modificando config.yaml.
DEFAULT_BEACON_INTERVAL="10800"
DEFAULT_ADVERT_ENABLED="false"
DEFAULT_ADVERT_INTERVAL="3600"
DEFAULT_FLOOD_ADVERT_ENABLED="false"
DEFAULT_FLOOD_ADVERT_INTERVAL="10800"

PROTOCOL_VERSION="1.0"

require_root() {
  if [[ ${EUID} -ne 0 ]]; then
    echo "Esegui questo script con sudo o come root."
    exit 1
  fi
}

log() {
  echo "[NEXUS-GATEWAY-INSTALL] $*"
}

prompt_default() {
  local var_name="$1" prompt="$2" default="$3"
  local value
  read -r -p "$prompt [$default]: " value
  value="${value:-$default}"
  printf -v "$var_name" '%s' "$value"
}

prompt_required() {
  local var_name="$1" prompt="$2"
  local value
  while true; do
    read -r -p "$prompt: " value
    if [[ -n "$value" ]]; then
      printf -v "$var_name" '%s' "$value"
      return
    fi
    echo "Campo obbligatorio, riprova."
  done
}

prompt_secret() {
  local var_name="$1" prompt="$2"
  local value
  read -r -s -p "$prompt: " value
  echo
  printf -v "$var_name" '%s' "$value"
}

select_user() {
  local sudo_user="${SUDO_USER:-}"
  if [[ -n "$sudo_user" && "$sudo_user" != "root" ]]; then
    SERVICE_USER="$sudo_user"
  else
    prompt_default SERVICE_USER "Utente Linux che eseguirà il servizio" "nexus"
  fi
  if ! id "$SERVICE_USER" >/dev/null 2>&1; then
    log "Creo l'utente $SERVICE_USER"
    adduser --disabled-password --gecos "" "$SERVICE_USER"
  fi
}

install_packages() {
  log "Installo dipendenze di sistema"
  apt-get update
  # python3-serial resta utile anche con companion TCP (troubleshooting
  # locale, bridge seriale-di-rete): installarlo sempre non ha controindicazioni.
  apt-get install -y python3 python3-venv python3-pip python3-serial mosquitto-clients curl
}

select_connection_mode() {
  prompt_default CONN_MODE "Tipo di connessione al Companion (serial/tcp)" "$DEFAULT_CONN_MODE"
  CONN_MODE="$(echo "$CONN_MODE" | tr '[:upper:]' '[:lower:]')"
  if [[ "$CONN_MODE" != "serial" && "$CONN_MODE" != "tcp" ]]; then
    log "Valore '$CONN_MODE' non valido, uso '$DEFAULT_CONN_MODE'."
    CONN_MODE="$DEFAULT_CONN_MODE"
  fi
}

detect_serial() {
  local detected
  detected=$(ls /dev/ttyUSB* /dev/ttyACM* 2>/dev/null | head -n1 || true)
  prompt_default SERIAL_PORT "Porta seriale del Companion" "${detected:-/dev/ttyUSB0}"
  prompt_default BAUDRATE "Baudrate seriale Companion" "$DEFAULT_BAUD"
}

configure_tcp() {
  log "Companion raggiungibile via rete (stesso tipo di connessione usata da trace-mon)"
  prompt_required MESHCORE_HOST "Hostname/IP del Companion (TCP)"
  prompt_default MESHCORE_PORT "Porta TCP del Companion" "$DEFAULT_TCP_PORT"
}

# Sostituisce il vecchio probe via `meshcli get_channels`: il progetto non
# dipende piu' da meshcore-cli, quindi la lettura canali passa da un piccolo
# script Python che usa direttamente la libreria meshcore_py (la stessa usata
# dal gateway in nexus_gateway/meshcore_adapter.py) invece del binario meshcli.
probe_channels() {
  # Lo script vive nella cartella del repository clonato (non serve
  # installarlo in $APP_DIR: non fa parte del servizio in esecuzione, serve
  # solo qui e per eventuali test manuali successivi da questa stessa cartella).
  local probe_script="$REPO_DIR/scripts/probe_meshcore.py"
  if [[ ! -x "$APP_DIR/.venv/bin/python" || ! -f "$probe_script" ]]; then
    return 0
  fi
  log "Provo a leggere i canali MeshCore (via libreria meshcore_py)"
  local cmd="$APP_DIR/.venv/bin/python $probe_script --mode $CONN_MODE --timeout $CMD_TIMEOUT"
  if [[ "$CONN_MODE" == "tcp" ]]; then
    cmd="$cmd --host $MESHCORE_HOST --port $MESHCORE_PORT"
  else
    cmd="$cmd --serial-port $SERIAL_PORT --baudrate $BAUDRATE"
  fi
  if su - "$SERVICE_USER" -c "$cmd" </dev/null; then
    true
  else
    log "Lettura canali non riuscita ora. Continuo comunque con l'installazione."
  fi
}

write_config() {
  mkdir -p "$APP_DIR"
  cat > "$APP_DIR/config.yaml" <<EOF
 gateway_id: $GATEWAY_ID
 site_name: "$SITE_NAME"
 region: $REGION
 mesh_id: $MESH_ID
 radio_band: "$RADIO_BAND"
 channel_name: $CHANNEL_NAME
 channel_number: $CHANNEL_NUMBER
 channel_scope: "$CHANNEL_SCOPE"
 default_scope: "$DEFAULT_SCOPE"
 channel_secret: "$CHANNEL_SECRET"
 path_hash_mode: $PATH_HASH_MODE
 protocol_version: "$PROTOCOL_VERSION"

 meshcore:
EOF
  if [[ "$CONN_MODE" == "tcp" ]]; then
    cat >> "$APP_DIR/config.yaml" <<EOF
   mode: tcp
   host: $MESHCORE_HOST
   port: $MESHCORE_PORT
EOF
  else
    cat >> "$APP_DIR/config.yaml" <<EOF
   mode: serial
   serial_port: $SERIAL_PORT
   baudrate: $BAUDRATE
EOF
  fi
  cat >> "$APP_DIR/config.yaml" <<EOF
   auto_reconnect: $AUTO_RECONNECT
   max_reconnect_attempts: $MAX_RECONNECT_ATTEMPTS
   command_timeout_sec: $CMD_TIMEOUT

 mqtt:
   host: $MQTT_HOST
   port: $MQTT_PORT
   username: $MQTT_USERNAME
   password: $MQTT_PASSWORD
   keepalive: 30
   tls: $MQTT_TLS
   uplink_topic: nexus/v1/uplink
   downlink_topic: nexus/v1/downlink/$GATEWAY_ID
   heartbeat_topic: nexus/v1/heartbeat/$GATEWAY_ID
   status_topic: nexus/v1/status/$GATEWAY_ID

 runtime:
   dedupe_ttl_sec: $DEDUPE_TTL
   heartbeat_interval_sec: $HEARTBEAT_INTERVAL
   poll_interval_sec: $POLL_INTERVAL
   watchdog_failure_threshold: $WATCHDOG_THRESHOLD
   log_level: INFO
EOF
  if [[ -n "$BEACON_TEXT" ]]; then
    cat >> "$APP_DIR/config.yaml" <<EOF
   beacon_interval_sec: $DEFAULT_BEACON_INTERVAL
   beacon_channel: $CHANNEL_NUMBER
   beacon_text: "$BEACON_TEXT"
EOF
  fi
  cat >> "$APP_DIR/config.yaml" <<EOF
   advert_enabled: $DEFAULT_ADVERT_ENABLED
   advert_interval_sec: $DEFAULT_ADVERT_INTERVAL
   flood_advert_enabled: $DEFAULT_FLOOD_ADVERT_ENABLED
   flood_advert_interval_sec: $DEFAULT_FLOOD_ADVERT_INTERVAL
EOF
  sed -i 's/^ //g' "$APP_DIR/config.yaml"
  chown "$SERVICE_USER":"$SERVICE_USER" "$APP_DIR/config.yaml"
  chmod 600 "$APP_DIR/config.yaml"
}

install_app_files() {
  log "Copio i file applicativi in $APP_DIR"
  mkdir -p "$APP_DIR/nexus_gateway"
  cp -r nexus_gateway "$APP_DIR/"
  # scripts/ (probe_meshcore.py, migrate_config.py) non va copiato in
  # $APP_DIR: non fa parte del servizio in esecuzione. probe_meshcore.py
  # viene usato dal probe qui sotto e per eventuali test manuali successivi
  # direttamente dalla cartella del repo clonato (vedi README/INSTALL).
  cp requirements.txt "$APP_DIR/"
  cp config.example.yaml "$APP_DIR/"
  python3 -m venv "$APP_DIR/.venv"
  "$APP_DIR/.venv/bin/pip" install --upgrade pip
  # requirements.txt ora installa la libreria "meshcore" (usata direttamente
  # dal gateway, vedi nexus_gateway/meshcore_adapter.py) al posto del vecchio
  # pacchetto "meshcore-cli"/binario meshcli.
  "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"
  chown -R "$SERVICE_USER":"$SERVICE_USER" "$APP_DIR"
}

configure_user_access() {
  usermod -a -G dialout "$SERVICE_USER" || true
}

write_service() {
  sed "s/__SERVICE_USER__/$SERVICE_USER/g" systemd/nexus-gateway.service > "$SERVICE_FILE"
  systemctl daemon-reload
}

start_service() {
  systemctl enable nexus-gateway
  systemctl restart nexus-gateway
}

print_summary() {
  local test_cmd
  if [[ "$CONN_MODE" == "tcp" ]]; then
    test_cmd="$APP_DIR/.venv/bin/python $REPO_DIR/scripts/probe_meshcore.py --mode tcp --host $MESHCORE_HOST --port $MESHCORE_PORT"
  else
    test_cmd="$APP_DIR/.venv/bin/python $REPO_DIR/scripts/probe_meshcore.py --mode serial --serial-port $SERIAL_PORT --baudrate $BAUDRATE"
  fi
  cat <<EOF

Installazione completata.

Comandi utili:
  sudo systemctl status nexus-gateway --no-pager
  journalctl -u nexus-gateway -f
  sudo systemctl restart nexus-gateway

Config:
  $APP_DIR/config.yaml

Test MeshCore (lettura canali via meshcore_py):
  sudo -u $SERVICE_USER $test_cmd

Parametri avanzati non richiesti a prompt (auto_reconnect, max_reconnect_attempts,
command_timeout_sec, watchdog_failure_threshold, path_hash_mode, advert_enabled,
advert_interval_sec, flood_advert_enabled, flood_advert_interval_sec) sono stati
scritti in config.yaml con valori di default sicuri: modificali a mano nel file
se necessario e riavvia il servizio.
EOF
}

main() {
  require_root
  cd "$(dirname "$0")"
  REPO_DIR="$(pwd)"
  select_user
  install_packages
  install_app_files
  configure_user_access

  select_connection_mode
  if [[ "$CONN_MODE" == "serial" ]]; then
    detect_serial
  else
    configure_tcp
  fi

  prompt_default GATEWAY_ID "Gateway ID" "NEXUS-ITALIA-[sigla provincia]"
  prompt_default SITE_NAME "Nome sito" "NEXUS-ITALIA [provincia]"
  prompt_default REGION "Regione/area" "[regione]"
  prompt_default MESH_ID "Mesh ID locale" "mesh-[provincia]"
  prompt_default RADIO_BAND "Banda radio" "868"
  prompt_default CHANNEL_NAME "Nome canale MeshCore" "NEXUS"
  prompt_default CHANNEL_NUMBER "Numero canale MeshCore" "1"
  prompt_default CHANNEL_SCOPE "Scope canale Nexus (es. it-lom-mi)" "$DEFAULT_CHANNEL_SCOPE"
  prompt_default DEFAULT_SCOPE "Scope di default per il flood" "$DEFAULT_DEFAULT_SCOPE"
  prompt_default CHANNEL_SECRET "Secret (hex) del canale Nexus" "$DEFAULT_CHANNEL_SECRET"

  PATH_HASH_MODE="$DEFAULT_PATH_HASH_MODE"
  AUTO_RECONNECT="$DEFAULT_AUTO_RECONNECT"
  MAX_RECONNECT_ATTEMPTS="$DEFAULT_MAX_RECONNECT_ATTEMPTS"
  CMD_TIMEOUT="$DEFAULT_CMD_TIMEOUT"
  WATCHDOG_THRESHOLD="$DEFAULT_WATCHDOG_THRESHOLD"

  probe_channels

  prompt_default MQTT_HOST "Host/IP broker MQTT" "nexus.meshcoreitalia.it"
  prompt_default MQTT_PORT "Porta broker MQTT" "1883"
  prompt_default MQTT_USERNAME "Username MQTT" "$GATEWAY_ID"
  prompt_secret MQTT_PASSWORD "Password MQTT"
  prompt_default MQTT_TLS "Usare TLS (true/false)" "false"

  prompt_default DEDUPE_TTL "TTL deduplica (secondi)" "$DEFAULT_DEDUPE"
  prompt_default HEARTBEAT_INTERVAL "Intervallo heartbeat/health-check (secondi)" "$DEFAULT_HEARTBEAT"
  prompt_default POLL_INTERVAL "Tetto massimo attesa nuovi messaggi uplink (secondi)" "$DEFAULT_POLL"

  prompt_default BEACON_TEXT "Testo beacon periodico (vuoto = disabilitato)" ""

  write_config
  write_service
  start_service
  print_summary
}

main "$@"
