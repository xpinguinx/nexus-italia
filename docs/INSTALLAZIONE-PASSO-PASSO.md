# Installazione passo passo del gateway NEXUS-ITALIA

## 1. Preparazione broker MQTT

Prima di installare il gateway, sul broker devono esistere:

- utente MQTT uguale al `gateway_id`
- ACL coerenti con i topic:
  - `nexus/v1/uplink`
  - `nexus/v1/downlink/<gateway_id>`
  - `nexus/v1/heartbeat/<gateway_id>`
  - `nexus/v1/status/<gateway_id>`

## 2. Collegare il Companion USB

Verifica che il sistema lo veda:

```bash
ls /dev/ttyUSB* /dev/ttyACM* 2>/dev/null
```

## 3. Lanciare l'installer

```bash
sudo bash install_gateway.sh
```

## 4. Rispondere ai prompt

I campi principali sono:

- `gateway_id`: per esempio `NEXUS-ITALIA-RM`
- `site_name`: descrizione del sito
- `channel_name`: per esempio `NEXUS`
- `channel_number`: per esempio `1`
- `mqtt_host`: IP o hostname del broker
- `mqtt_username`: in genere uguale al `gateway_id`

## 5. Verificare il servizio

```bash
sudo systemctl status nexus-gateway --no-pager
journalctl -u nexus-gateway -f
```

## 6. Verificare il traffico lato broker

Sul server broker/router:

```bash
mosquitto_sub -h 127.0.0.1 -p 1883 -u router -P 'PASSWORD_ROUTER' -t 'nexus/v1/#' -v
```

## 7. Test radio locale

Sul Raspberry:

```bash
sudo -u <utente-servizio> /opt/nexus-gateway/.venv/bin/meshcli -j -s /dev/ttyUSB0 -b 115200 chan 1 "test nexus"
```
