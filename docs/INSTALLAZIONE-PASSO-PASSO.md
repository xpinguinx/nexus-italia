# Installazione passo passo del gateway NEXUS-ITALIA

## 1. Preparazione broker MQTT

Prima di installare il gateway, sul broker devono esistere:

- utente MQTT uguale al `gateway_id`
- ACL coerenti con i topic:
  - `nexus/v1/uplink`
  - `nexus/v1/downlink/<gateway_id>`
  - `nexus/v1/heartbeat/<gateway_id>`
  - `nexus/v1/status/<gateway_id>`

## 2. Collegare il Companion

Il Companion MeshCore può essere collegato in due modi, scelti durante l'installazione:

- **seriale**: via USB al Raspberry. Verifica che il sistema lo veda:

  ```bash
  ls /dev/ttyUSB* /dev/ttyACM* 2>/dev/null
  ```

- **TCP**: raggiungibile in rete (companion con firmware TCP/WiFi abilitato, oppure un bridge seriale-di-rete come ser2net/socat davanti al ttyUSB del device). Serve conoscere in anticipo l'hostname/IP e la porta del companion.

## 3. Lanciare l'installer

Per un gateway nuovo:

```bash
sudo bash install_gateway.sh
```

Per aggiornare un gateway esistente installato con una versione precedente (basata su `meshcli`) alla nuova versione con supporto TCP, vedi il punto 6 più sotto — non rilanciare `install_gateway.sh` su un'installazione già in produzione: sovrascriverebbe `config.yaml` da zero.

## 4. Rispondere ai prompt

I campi principali sono:

- tipo di connessione al Companion: `serial` o `tcp` (default `serial`)
  - se `serial`: porta seriale (es. `/dev/ttyUSB0`) e baudrate
  - se `tcp`: host/IP e porta del Companion
- `gateway_id`: per esempio `NEXUS-ITALIA-RM`
- `site_name`: descrizione del sito
- `channel_name`: per esempio `NEXUS`
- `channel_number`: per esempio `1`
- `channel_scope` / scope di default: ambito del canale Nexus (es. `it-lom-mi` per Milano)
- secret del canale Nexus (vedi il README per il valore condiviso)
- `mqtt_host`: IP o hostname del broker
- `mqtt_username`: in genere uguale al `gateway_id`

Alcuni parametri più avanzati (resilienza della connessione al companion, soglia del watchdog, advert/flood advert periodici) non vengono chiesti a prompt: l'installer li scrive in `config.yaml` con valori di default sicuri, documentati con commenti in `config.example.yaml`. Puoi modificarli a mano dopo l'installazione e riavviare il servizio.

## 5. Verificare il servizio

```bash
sudo systemctl status nexus-gateway --no-pager
journalctl -u nexus-gateway -f
```

## 6. Migrare un'installazione esistente

Se il gateway era già installato con una versione precedente del repository (sezione `meshcli:` in `config.yaml`, solo seriale), aggiorna prima il repository clonato e poi lancia lo script di migrazione dedicato, invece di `install_gateway.sh`:

```bash
cd nexus-italia
git pull
sudo bash migrate_gateway.sh
```

Lo script ferma il servizio, fa un backup di `config.yaml` e del codice precedente, ricrea da zero il virtualenv con le nuove dipendenze, copia i nuovi file applicativi e propone un nuovo `config.yaml` (sezione `meshcli:` rinominata in `meshcore:`, valori esistenti preservati, solo i campi nuovi aggiunti con default sicuri). Mostra il diff proposto e chiede conferma prima di sovrascrivere `config.yaml`; in ogni caso, alla fine il servizio viene riavviato — se rifiuti la nuova configurazione, quella precedente resta in uso (il nuovo codice la supporta comunque) e la proposta di migrazione resta disponibile nella cartella di backup per una revisione manuale con calma.

## 7. Verificare il traffico lato broker

Sul server broker/router:

```bash
mosquitto_sub -h 127.0.0.1 -p 1883 -u router -P 'PASSWORD_ROUTER' -t 'nexus/v1/#' -v
```

## 8. Test radio locale

Il progetto non dipende più da `meshcli`: i test manuali passano dallo script `scripts/probe_meshcore.py` (incluso nell'installazione), che usa direttamente la libreria `meshcore`.

Lettura canali:

```bash
sudo -u <utente-servizio> /opt/nexus-gateway/.venv/bin/python /opt/nexus-gateway/scripts/probe_meshcore.py --mode serial --serial-port /dev/ttyUSB0 --baudrate 115200
```

(sostituisci `--mode serial --serial-port ... --baudrate ...` con `--mode tcp --host ... --port ...` se il companion è in TCP)

Invio di un messaggio di test sul canale:

```bash
sudo -u <utente-servizio> /opt/nexus-gateway/.venv/bin/python /opt/nexus-gateway/scripts/probe_meshcore.py --mode serial --serial-port /dev/ttyUSB0 --send-text "test nexus" --channel 1
```
