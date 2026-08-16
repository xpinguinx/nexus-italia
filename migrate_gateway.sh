#!/usr/bin/env bash
set -euo pipefail

# Migra un'installazione ESISTENTE del gateway (creata con una versione
# precedente di install_gateway.sh, basata su meshcli/meshcore-cli) alla
# nuova versione basata sulla libreria meshcore_py, con supporto TCP e
# resilienza connessione.
#
# A differenza di install_gateway.sh (pensato per un'installazione da zero,
# con prompt interattivi per ogni parametro), questo script legge i valori
# gia' presenti in un'installazione funzionante e li preserva, aggiungendo
# solo cio' che serve per il nuovo schema. Non richiede quasi nessun input.
#
# Uso:
#   sudo bash migrate_gateway.sh
#
# Cosa fa, in ordine:
#   1. individua l'installazione esistente in APP_DIR (default /opt/nexus-gateway)
#   2. ferma il servizio
#   3. fa un backup di config.yaml e del vecchio nexus_gateway/ in una
#      sottocartella di migrazione con timestamp
#   4. elimina il vecchio ambiente .venv e lo ricrea da zero con il nuovo
#      requirements.txt (libreria "meshcore" al posto di "meshcore-cli")
#   5. copia i nuovi file applicativi (nexus_gateway/, scripts/, ...)
#   6. genera una proposta di nuovo config.yaml (scripts/migrate_config.py:
#      sposta "meshcli:" in "meshcore:", preserva tutti i valori esistenti,
#      aggiunge solo i campi nuovi con default sicuri) e ne mostra il diff
#      rispetto all'originale PRIMA di applicarlo
#   7. chiede conferma esplicita prima di sovrascrivere config.yaml
#   8. riavvia il servizio in ogni caso (il nuovo codice resta compatibile
#      con un config.yaml non ancora migrato, quindi il gateway non resta
#      mai giu' per colpa di questo script)

APP_DIR="/opt/nexus-gateway"
SERVICE_NAME="nexus-gateway"

log() {
  echo "[NEXUS-GATEWAY-MIGRATE] $*"
}

require_root() {
  if [[ ${EUID} -ne 0 ]]; then
    echo "Esegui questo script con sudo o come root."
    exit 1
  fi
}

check_existing_install() {
  if [[ ! -f "$APP_DIR/config.yaml" ]]; then
    echo "Nessuna installazione esistente trovata in $APP_DIR (config.yaml assente)."
    echo "Per una nuova installazione usa install_gateway.sh, non questo script."
    exit 1
  fi
  if [[ ! -d "$APP_DIR/nexus_gateway" ]]; then
    echo "$APP_DIR/config.yaml esiste ma $APP_DIR/nexus_gateway non c'e': installazione anomala, interrompo."
    exit 1
  fi
}

detect_service_user() {
  SERVICE_USER="$(stat -c '%U' "$APP_DIR/config.yaml")"
  if [[ -z "$SERVICE_USER" || "$SERVICE_USER" == "root" ]]; then
    log "Impossibile determinare con certezza l'utente del servizio dal proprietario di config.yaml (trovato: '$SERVICE_USER')."
    read -r -p "Utente Linux che esegue il servizio: " SERVICE_USER
  fi
  log "Utente del servizio: $SERVICE_USER"
}

stop_service() {
  log "Arresto il servizio $SERVICE_NAME (se attivo)"
  systemctl stop "$SERVICE_NAME" 2>/dev/null || true
}

make_backup() {
  BACKUP_DIR="$APP_DIR/migration-backup-$(date +%Y%m%d%H%M%S)"
  mkdir -p "$BACKUP_DIR"
  log "Backup di sicurezza in $BACKUP_DIR"
  cp "$APP_DIR/config.yaml" "$BACKUP_DIR/config.yaml.bak"
  cp -r "$APP_DIR/nexus_gateway" "$BACKUP_DIR/nexus_gateway.bak"
}

rebuild_venv_and_app_files() {
  log "Rimuovo il vecchio ambiente .venv"
  rm -rf "$APP_DIR/.venv"

  log "Copio i nuovi file applicativi in $APP_DIR"
  rm -rf "$APP_DIR/nexus_gateway"
  mkdir -p "$APP_DIR/nexus_gateway"
  cp -r nexus_gateway "$APP_DIR/"
  # scripts/ non va copiato in $APP_DIR: non fa parte del servizio in
  # esecuzione. migrate_config.py viene invocato solo da questo script,
  # dalla cartella del repo clonato (vedi migrate_config() piu' sotto);
  # probe_meshcore.py, se serve un test manuale dopo la migrazione, si
  # esegue allo stesso modo da li'.
  rm -rf "$APP_DIR/scripts"  # pulizia di un eventuale scripts/ da installazioni precedenti
  cp requirements.txt "$APP_DIR/"
  cp config.example.yaml "$APP_DIR/"

  log "Ricreo l'ambiente .venv con il nuovo requirements.txt"
  python3 -m venv "$APP_DIR/.venv"
  "$APP_DIR/.venv/bin/pip" install --upgrade pip
  # requirements.txt installa ora la libreria "meshcore" (usata direttamente
  # dal gateway) al posto del vecchio pacchetto "meshcore-cli"/binario meshcli.
  "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

  chown -R "$SERVICE_USER":"$SERVICE_USER" "$APP_DIR"
}

migrate_config() {
  local migrated="$BACKUP_DIR/config.yaml.migrated"
  log "Genero la proposta di nuovo config.yaml (meshcli: -> meshcore:, aggiunta campi nuovi)"
  if ! "$APP_DIR/.venv/bin/python" scripts/migrate_config.py "$BACKUP_DIR/config.yaml.bak" "$migrated"; then
    log "Migrazione automatica di config.yaml non riuscita."
    log "config.yaml ORIGINALE resta in uso (il nuovo codice e' comunque compatibile con lo schema vecchio)."
    log "Rivedi a mano $BACKUP_DIR/config.yaml.bak, poi eventualmente rilancia questo script."
    CONFIG_MIGRATED=0
    return
  fi

  log "Verifico che il nuovo config.yaml sia valido (lo carico con il nuovo codice, senza applicarlo)"
  if ! "$APP_DIR/.venv/bin/python" -c "
import sys
sys.path.insert(0, '$APP_DIR')
from nexus_gateway.config import load_config
load_config('$migrated')
"; then
    log "Il config.yaml migrato non supera la validazione: NON viene applicato."
    log "config.yaml ORIGINALE resta in uso. Proposta di migrazione consultabile in: $migrated"
    CONFIG_MIGRATED=0
    return
  fi

  echo
  echo "Differenze proposte tra il config.yaml attuale e quello migrato:"
  echo "-----------------------------------------------------------------"
  diff -u "$BACKUP_DIR/config.yaml.bak" "$migrated" || true
  echo "-----------------------------------------------------------------"
  echo

  local answer
  read -r -p "Applicare questa nuova configurazione a $APP_DIR/config.yaml? (y/N): " answer
  if [[ "$answer" =~ ^[Yy]$ ]]; then
    cp "$migrated" "$APP_DIR/config.yaml"
    chown "$SERVICE_USER":"$SERVICE_USER" "$APP_DIR/config.yaml"
    chmod 600 "$APP_DIR/config.yaml"
    log "config.yaml aggiornato."
    CONFIG_MIGRATED=1
  else
    log "config.yaml NON modificato per scelta dell'operatore."
    log "config.yaml ORIGINALE resta in uso (il nuovo codice e' comunque compatibile con lo schema vecchio)."
    log "Proposta di migrazione consultabile in: $migrated"
    CONFIG_MIGRATED=0
  fi
}

restart_service() {
  log "Riavvio il servizio $SERVICE_NAME"
  systemctl daemon-reload
  systemctl restart "$SERVICE_NAME"
}

print_summary() {
  cat <<EOF

Migrazione completata.

Backup della configurazione/codice precedenti:
  $BACKUP_DIR

Config attiva:
  $APP_DIR/config.yaml
EOF
  if [[ "$CONFIG_MIGRATED" -eq 0 ]]; then
    cat <<EOF

NOTA: config.yaml non e' stato sostituito automaticamente (vedi sopra il motivo).
Il gateway funziona comunque con lo schema precedente ("meshcli:"), il nuovo
codice lo supporta ancora. Quando vuoi, applica a mano la proposta di
migrazione o rilancia questo script.
EOF
  fi
  cat <<EOF

Comandi utili:
  sudo systemctl status $SERVICE_NAME --no-pager
  journalctl -u $SERVICE_NAME -f
EOF
}

main() {
  require_root
  cd "$(dirname "$0")"
  check_existing_install
  detect_service_user
  stop_service
  make_backup
  rebuild_venv_and_app_files
  CONFIG_MIGRATED=0
  migrate_config
  restart_service
  print_summary
}

main "$@"
