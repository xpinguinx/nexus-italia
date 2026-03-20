#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/nexus-gateway"
SERVICE_FILE="/etc/systemd/system/nexus-gateway.service"
DEFAULT_BAUD="115200"
DEFAULT_POLL="5"
DEFAULT_HEARTBEAT="30"
DEFAULT_DEDUPE="180"
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
  apt-get install -y python3 python3-venv python3-pip python3-serial mosquitto-clients curl
}

detect_serial() {
  local detected
  detected=$(ls /dev/ttyUSB* /dev/ttyACM* 2>/dev/null | head -n1 || true)
  prompt_default SERIAL_PORT "Porta seriale del Companion USB" "${detected:-/dev/ttyUSB0}"
}

probe_channels() {
  if [[ -x "$APP_DIR/.venv/bin/meshcli" ]]; then
    log "Provo a leggere i canali MeshCore"
    if su - "$SERVICE_USER" -c "$APP_DIR/.venv/bin/meshcli -j -s $SERIAL_PORT -b $BAUDRATE get_channels"; then
      true
    else
      log "Lettura canali non riuscita ora. Continuo comunque con l'installazione."
    fi
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
 protocol_version: "$PROTOCOL_VERSION"
 
 meshcli:
   command: $APP_DIR/.venv/bin/meshcli
   serial_port: $SERIAL_PORT
   baudrate: $BAUDRATE
   timeout_sec: 10
   mode: serial
 
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
   log_level: INFO
EOF
  sed -i 's/^ //g' "$APP_DIR/config.yaml"
  chown "$SERVICE_USER":"$SERVICE_USER" "$APP_DIR/config.yaml"
  chmod 600 "$APP_DIR/config.yaml"
}

install_app_files() {
  log "Copio i file applicativi in $APP_DIR"
  mkdir -p "$APP_DIR/nexus_gateway"
  cp -r nexus_gateway "$APP_DIR/"
  cp requirements.txt "$APP_DIR/"
  cp config.example.yaml "$APP_DIR/"
  python3 -m venv "$APP_DIR/.venv"
  "$APP_DIR/.venv/bin/pip" install --upgrade pip
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
  cat <<EOF

Installazione completata.

Comandi utili:
  sudo systemctl status nexus-gateway --no-pager
  journalctl -u nexus-gateway -f
  sudo systemctl restart nexus-gateway

Config:
  $APP_DIR/config.yaml

Test MeshCore:
  sudo -u $SERVICE_USER $APP_DIR/.venv/bin/meshcli -j -s $SERIAL_PORT -b $BAUDRATE get_channels
  sudo -u $SERVICE_USER $APP_DIR/.venv/bin/meshcli -j -s $SERIAL_PORT -b $BAUDRATE sync_msgs
EOF
}

main() {
  require_root
  cd "$(dirname "$0")"
  select_user
  install_packages
  install_app_files
  configure_user_access
  detect_serial
  prompt_default GATEWAY_ID "Gateway ID" "NEXUS-ITALIA-[sigla provincia]"
  prompt_default SITE_NAME "Nome sito" "NEXUS-ITALIA [provincia]"
  prompt_default REGION "Regione/area" "[regione]"
  prompt_default MESH_ID "Mesh ID locale" "mesh-[provincia]"
  prompt_default RADIO_BAND "Banda radio" "868"
  prompt_default CHANNEL_NAME "Nome canale MeshCore" "NEXUS"
  prompt_default CHANNEL_NUMBER "Numero canale MeshCore" "1"
  prompt_default BAUDRATE "Baudrate seriale Companion" "$DEFAULT_BAUD"
  probe_channels
  prompt_default MQTT_HOST "Host/IP broker MQTT" "nexus.meshcoreitalia.it"
  prompt_default MQTT_PORT "Porta broker MQTT" "1883"
  prompt_default MQTT_USERNAME "Username MQTT" "$GATEWAY_ID"
  prompt_secret MQTT_PASSWORD "Password MQTT"
  prompt_default MQTT_TLS "Usare TLS (true/false)" "false"
  prompt_default DEDUPE_TTL "TTL deduplica (secondi)" "$DEFAULT_DEDUPE"
  prompt_default HEARTBEAT_INTERVAL "Intervallo heartbeat (secondi)" "$DEFAULT_HEARTBEAT"
  prompt_default POLL_INTERVAL "Intervallo polling meshcli (secondi)" "$DEFAULT_POLL"
  write_config
  write_service
  start_service
  print_summary
}

main "$@"
