# NEXUS-ITALIA Gateway Installer

Installer automatico per gateway **NEXUS-ITALIA** basato su Raspberry Pi e Companion MeshCore, collegato via USB seriale oppure raggiungibile via rete (TCP).

Questo repository installa e configura in automatico:

- dipendenze di sistema
- ambiente Python dedicato
- libreria `meshcore` (accesso diretto al companion, niente più `meshcore-cli`/binario `meshcli`) dentro il virtualenv del gateway
- configurazione `config.yaml`
- servizio `systemd` `nexus-gateway`
- avvio automatico al boot

## Requisiti

- Raspberry Pi OS / Debian / Ubuntu (NO desktop)
- accesso Internet
- Companion MeshCore raggiungibile, in una delle due modalità:
  - **seriale**: collegato via USB al Raspberry (es. `/dev/ttyUSB0`)
  - **TCP**: raggiungibile in rete (companion con firmware TCP/WiFi abilitato, oppure un bridge seriale-di-rete come ser2net/socat davanti al ttyUSB del device)
- credenziali MQTT da richiedere all'indirizzo email info@meshcoreitalia.it

## Creazione canale NEXUS con relativa Secret Key

<img width="302" height="399" alt="nexus" src="https://github.com/user-attachments/assets/8b4a8b6f-4050-4015-a9d1-3f626b3de48f" />

Nome Canale: Nexus

Secret Key: a45768ab48e203498edbc11b35cdfbd7

## Installazione rapida (nuovo gateway)

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
- tipo di connessione al Companion (seriale o TCP) e relativi parametri (porta seriale/baudrate, oppure host/porta TCP)
- `gateway_id`, dati radio locali
- nome/numero canale MeshCore, scope del canale, secret del canale Nexus
- host/porta/credenziali MQTT
- parametri di deduplica/heartbeat

Alcuni parametri più avanzati (resilienza della connessione, watchdog, advert/flood advert periodici) non vengono richiesti a prompt: vengono scritti in `config.yaml` con valori di default sicuri, e sono documentati con commenti in `config.example.yaml` — modificabili a mano dopo l'installazione.

## Migrazione da un'installazione precedente

Se hai già un gateway installato con una versione precedente di questo repository (basata su `meshcli`/`meshcore-cli`, solo seriale), **non** rilanciare `install_gateway.sh` — sovrascriverebbe `config.yaml` da zero. Usa invece:

```bash
cd nexus-italia
git pull
sudo bash migrate_gateway.sh
```

Lo script:

- individua l'installazione esistente (di default in `/opt/nexus-gateway`) e l'utente che esegue il servizio
- ferma il servizio e fa un backup di sicurezza di `config.yaml` e del codice precedente, con timestamp
- elimina il vecchio virtualenv e lo ricrea da zero con il nuovo `requirements.txt`
- copia i nuovi file applicativi
- propone un nuovo `config.yaml` (sposta `meshcli:` in `meshcore:`, **preserva tutti i valori esistenti** — porta seriale, baudrate, credenziali MQTT, ecc. — e aggiunge solo i campi nuovi con default sicuri), mostrandone il diff rispetto all'originale **prima** di applicarlo, e chiedendo conferma esplicita
- riavvia comunque il servizio alla fine, sia che tu accetti la nuova configurazione sia che tu la rifiuti: il nuovo codice resta compatibile con un `config.yaml` ancora nello schema vecchio, quindi il gateway non resta mai giù per colpa della migrazione

Se rifiuti la configurazione proposta, la trovi comunque pronta per la revisione manuale nella cartella di backup indicata a fine esecuzione.

## Valori verificati in test

Configurazione funzionante già verificata:

- `gateway_id`: `NEXUS-ITALIA-RM`
- seriale: `/dev/ttyUSB0`
- TCP: companion raggiunto via rete su porta 5000
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

Lo script aggiunge l'utente del servizio al gruppo `dialout` per l'accesso alla seriale (utile anche in modalità TCP, per eventuale troubleshooting locale).
Dopo l'installazione, se il Companion non viene visto subito dal servizio, può essere utile un riavvio del Raspberry.

## Test manuali MeshCore

Il progetto non dipende più da `meshcore-cli`/binario `meshcli`: i test manuali passano dallo script `scripts/probe_meshcore.py`, incluso nell'installazione, che usa direttamente la libreria `meshcore` (la stessa usata dal gateway).

Lettura canali - seriale:

```bash
sudo -u <utente-servizio> /opt/nexus-gateway/.venv/bin/python /opt/nexus-gateway/scripts/probe_meshcore.py --mode serial --serial-port /dev/ttyUSB0 --baudrate 115200
```

Lettura canali - TCP:

```bash
sudo -u <utente-servizio> /opt/nexus-gateway/.venv/bin/python /opt/nexus-gateway/scripts/probe_meshcore.py --mode tcp --host 192.168.1.50 --port 5000
```

Invio di un messaggio di test sul canale (radio):

```bash
sudo -u <utente-servizio> /opt/nexus-gateway/.venv/bin/python /opt/nexus-gateway/scripts/probe_meshcore.py --mode serial --serial-port /dev/ttyUSB0 --send-text "test nexus" --channel 1
```
