# NEXUS-ITALIA Gateway Installer

Installer automatico per gateway **NEXUS-ITALIA** basato su Raspberry Pi0 2W e Companion USB MeshCore.

Questo repository installa e configura in automatico:

- dipendenze di sistema
- ambiente Python dedicato
- `meshcore-cli` dentro il virtualenv del gateway
- configurazione `config.yaml`
- servizio `systemd` `nexus-gateway`
- avvio automatico al boot

## Requisiti

- Raspberry Pi OS / Debian / Ubuntu (NO desktop)
- accesso Internet
- Companion USB MeshCore collegato
- credenziali MQTT da richiedere all'indirizzo email info@meshcoreitalia.it

## Installazione rapida

Clona il repository e lancia lo script come root:

```bash
sudo apt update
sudo apt install -y git
git clone https://github.com/xpinguinx/nexus-italia.git
cd nexus-italia
sudo bash install_gateway.sh
```

Lo script chiede passo passo:

- utente Linux del servizio
- porta seriale del Companion
- `gateway_id`
- dati radio locali
- host/porta/credenziali MQTT
- nome e numero canale MeshCore

## Valori verificati in test

Configurazione funzionante già verificata:

- `gateway_id`: `NEXUS-ITALIA-RM`
- seriale: `/dev/ttyUSB0`
- canale MeshCore: `NEXUS`
- numero canale: `1`
- broker MQTT con autenticazione utente/password
- servizio avviato via `systemd`

## Comandi utili

Stato servizio:

```bash
sudo systemctl status nexus-gateway --no-pager
```

Log live:

```bash
journalctl -u nexus-gateway -f
```

Riavvio:

```bash
sudo systemctl restart nexus-gateway
```

## Percorsi installati

- applicazione: `/opt/nexus-gateway`
- configurazione: `/opt/nexus-gateway/config.yaml`
- servizio: `/etc/systemd/system/nexus-gateway.service`

## Note operative

Lo script aggiunge l'utente del servizio al gruppo `dialout` per l'accesso alla seriale.
Dopo l'installazione, se il Companion non viene visto subito dal servizio, può essere utile un riavvio del Raspberry.

## Test manuali MeshCore

```bash
sudo -u <utente-servizio> /opt/nexus-gateway/.venv/bin/meshcli -j -s /dev/ttyUSB0 -b 115200 get_channels
sudo -u <utente-servizio> /opt/nexus-gateway/.venv/bin/meshcli -j -s /dev/ttyUSB0 -b 115200 sync_msgs
```

