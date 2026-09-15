# OP17 Price Monitor

Pipeline che controlla periodicamente il prezzo del box Eng di **One Piece
Card Game OP-17 – The World's Strongest Warriors** su una lista di shop
europei e invia un'email quando il prezzo scende sotto una soglia (default:
**151 €**).

Gira come [GitHub Actions](../.github/workflows/op17-price-monitor.yml)
schedulato ogni 15 minuti, quindi non serve tenere nulla acceso in locale.

## Come funziona

1. `config/sites.yaml` contiene la lista degli shop da controllare (URL della
   pagina prodotto) e la soglia di prezzo.
2. `monitor.py` scarica ogni pagina ed estrae il prezzo provando, in ordine:
   dati strutturati JSON-LD (schema.org), meta tag Open Graph
   (`product:price:amount`), microdata (`itemprop="price"`), e infine una
   regex sul simbolo `€` come fallback.
3. Se il prezzo trovato è in EUR ed è sotto la soglia, invia un'email via
   Gmail SMTP. Lo stato viene salvato in `state.json` per non rimandare la
   stessa email ad ogni run (riavvisa solo se il prezzo scende ulteriormente).

## Setup

### 1. Crea una App Password Gmail

Serve perché Gmail non accetta login SMTP con la password normale:

1. Attiva la verifica in due passaggi sul tuo account Google (se non già
   attiva): https://myaccount.google.com/security
2. Crea una App Password: https://myaccount.google.com/apppasswords
3. Copia la password generata (16 caratteri).

### 2. Aggiungi i secret al repository

Su GitHub: **Settings → Secrets and variables → Actions → New repository
secret**, aggiungi:

| Nome | Valore |
|---|---|
| `GMAIL_USER` | il tuo indirizzo Gmail (mittente) |
| `GMAIL_APP_PASSWORD` | la App Password creata sopra |
| `ALERT_TO` | (opzionale) indirizzo a cui inviare l'alert, default = `GMAIL_USER` |

### 3. Configura shop e soglia

Modifica `config/sites.yaml`:

```yaml
threshold_eur: 151.0
sites:
  - name: "Nome negozio"
    url: "https://.../pagina-prodotto"
```

Shop già configurati come punto di partenza: Zatu Games (UK), PixelHeart
(FR), Candy Cards (IE), Oupi.eu (FR). **Verifica tu stesso ogni URL** prima
di fidarti degli alert: alcuni shop mostrano il prezzo in una valuta diversa
da EUR a seconda della lingua/paese selezionato nell'URL — in quel caso lo
script salta il confronto e lo segnala nei log invece di generare un falso
alert.

Per aggiungere altri negozi europei, basta aggiungere una nuova voce con
`name` e `url` alla lista `sites`.

### 4. Test manuale

Vai su **Actions → OP17 Price Monitor → Run workflow** per lanciare un
controllo subito, senza aspettare i 15 minuti, e verificare nei log che i
prezzi vengano estratti correttamente da tutti i siti configurati.

## Limiti noti

- **Non tutti i siti sono scrapable allo stesso modo.** Siti fortemente
  basati su JavaScript (contenuto renderizzato client-side, es. alcune
  pagine di marketplace) potrebbero non esporre il prezzo nell'HTML
  scaricato da `requests`; in quel caso `monitor.py` logga un warning e
  passa oltre senza bloccare gli altri shop.
- **Rispetta i termini di servizio dei siti monitorati**: non abbassare
  troppo l'intervallo del cron (15 minuti è già frequente) per non generare
  traffico eccessivo o farsi bloccare l'IP.
- **Amazon e simili non sono inclusi** di proposito: hanno protezioni
  anti-bot aggressive e ToS che vietano lo scraping automatizzato.
- Se il repo resta più di ~60 giorni senza commit (cioè il prezzo non
  cambia mai), GitHub disabilita automaticamente gli schedule: in quel caso
  vai su **Actions** e riattiva il workflow manualmente.
