# Deployment

Ziel: Der Essensplaner läuft als Container mit Docker Compose auf dem
Home-Server (Home-Server, Hostname `homeserver`), und jeder Push auf `main` rollt
ihn automatisch aus.

> **Vorgeschichte.** Geplant war Coolify auf einem Hetzner-Server. Das wurde
> aufgegeben: Der Server hatte 927 MB RAM, Coolify lief darauf nicht
> brauchbar, und der Server existiert nicht mehr. Der Rollout über Coolify
> ist deshalb hier nicht mehr beschrieben — die Deploy-Secrets waren ohnehin
> nie gesetzt, der Rollout-Schritt wurde bei jedem Lauf übersprungen.

---

## 1. Überblick

```
Push auf main
   │
   ├─ Prüfungen      Lint, 640+ Tests, Stammdaten, Probeplan, Image-Rauchtest
   │
   ├─ Image          Build und Push nach ghcr.io/<owner>/essensplaner
   │                 Tags: latest, sha-<commit>, <zeitstempel>
   │
   └─ Rollout        Runner tritt dem Tailnet bei
                     → SSH auf den Home-Server
                     → /usr/local/bin/essensplaner-deploy
                     → docker compose pull && up -d, dann Healthcheck
```

Der Rollout hängt an `needs: image`, und das Image hängt an den Prüfungen. Ein
Rezept mit Fenchel oder ein Montagsgericht unter 30 g Protein lässt die
Pipeline rot werden, bevor irgendetwas den Server erreicht.

### Warum Tailscale und kein Webhook

Der Home-Server steht im Heimnetz unter einer privaten Adresse. GitHub kann ihn nicht
erreichen, ein Webhook im Wortsinn ist also nicht möglich, ohne einen Weg von
außen hinein zu öffnen. Drei Wege wurden erwogen:

| Weg | Preis |
|---|---|
| Portweiterleitung am Gateway | ein selbstgebauter Dienst im offenen Internet, plus DynDNS und TLS |
| Cloudflare Tunnel | Cloudflare-Konto und dort verwaltete Domain, plus ein Webhook-Empfänger als Dienst |
| **Tailscale** | Tailscale-Konto und ein OAuth-Client — kein offener Port |

Gewählt wurde Tailscale: Der Runner tritt als kurzlebiger, getaggter Knoten
dem Tailnet bei und verschwindet danach wieder. Am Gateway ist nichts offen,
und der Home-Server ist nebenbei auch von unterwegs erreichbar.

### Architekturen

Die Pipeline baut jedes Image für `linux/amd64` **und** `linux/arm64`. Unter
einem Tag wie `latest` liegt deshalb keine einzelne Datei, sondern eine Liste,
aus der Docker beim Ziehen die passende Fassung nimmt. Derselbe Tag läuft
damit auf dem x86-Server, auf ARM-Servern und auf einem Raspberry Pi mit
64-Bit-System – ohne dass irgendwo etwas umzustellen wäre.

Prüfen lässt sich das ohne Docker:

```bash
TOKEN=$(curl -s "https://ghcr.io/token?service=ghcr.io&scope=repository:<owner>/essensplaner:pull" | sed -n 's/.*"token":"\([^"]*\)".*/\1/p')
```

```bash
curl -s -H "Authorization: Bearer $TOKEN" -H "Accept: application/vnd.oci.image.index.v1+json" "https://ghcr.io/v2/<owner>/essensplaner/manifests/latest"
```

In der Antwort steht je ein Eintrag mit `"architecture":"amd64"` und
`"architecture":"arm64"`.

---

## 2. Der Home-Server als Ziel

### 2.1 Verzeichnis und Dateien

Alles liegt unter `/opt/essensplaner`, Eigentümer `benutzer`:

| Datei | Herkunft | Zweck |
|---|---|---|
| `docker-compose.yml` | Kopie aus dem Repo | Dienst, Volume, Healthcheck |
| `docker-compose.override.yml` | nur auf dem Home-Server | veröffentlicht Port 8080 im LAN |
| `.env` | nur auf dem Home-Server, `chmod 600` | Konfiguration und Geheimnisse |

Die Repo-Datei bleibt bewusst unverändert. Der Override enthält allein das,
was den Home-Server vom Betrieb hinter einem Proxy unterscheidet:

```yaml
services:
  essensplaner:
    ports:
      - "8080:8080"
```

Ändert sich `docker-compose.yml` im Repo, muss die Kopie von Hand
nachgezogen werden — das Deploy-Skript aktualisiert nur das Image:

```bash
scp docker-compose.yml benutzer@homeserver:/opt/essensplaner/
```

### 2.2 Image öffentlich oder privat

Das Paket `ghcr.io/<owner>/essensplaner` ist derzeit **öffentlich**. Dadurch
braucht der Home-Server keine Registry-Zugangsdaten.

Zu bedenken: Das Repo ist privat, das Image enthält aber den Code samt
Rezepten und Haushaltskonfiguration. Wer das Image zieht, kann beides lesen.
Zugangsdaten sind nicht im Image — die kommen zur Laufzeit aus `.env`.

Soll das Paket privat werden, braucht der Home-Server einen Login:

```bash
ssh benutzer@homeserver 'docker login ghcr.io -u <github-user>'
```

Als Passwort ein Personal Access Token mit `read:packages`. Der Login
landet in `/home/benutzer/.docker/config.json` und überlebt Neustarts.

### 2.3 Umgebungsvariablen

In `/opt/essensplaner/.env`, Rechte `600`:

| Variable | Wert auf dem Home-Server | Pflicht |
|---|---|---|
| `TZ` | `Europe/Berlin` | ja |
| `ESSENSPLANER_IMAGE` | `ghcr.io/<owner>/essensplaner:latest` | ja |
| `SMTP_HOST` | derzeit leer | nein, siehe unten |
| `SMTP_PORT` | `587` | – |
| `SMTP_USER` | – | mit Mail |
| `SMTP_PASSWORD` | – | mit Mail |
| `SMTP_STARTTLS` | `true` | – |
| `SMTP_SSL` | `false` | – |
| `MAIL_FROM` / `MAIL_TO` | – | mit Mail |
| `ESSENSPLANER_WEB_PASSWORT` | gesetzt (generiert) | für die Oberfläche |
| `ESSENSPLANER_WEB_SECRET` | gesetzt (generiert) | empfohlen |
| `ESSENSPLANER_WEB_PORT` | `8080` | – |
| `ESSENSPLANER_WEB_UNSICHER` | `true` | siehe 2.4 |
| `ESSENSPLANER_FREEZER` | `false` | – |
| `ESSENSPLANER_SEED` | leer | – |

**Ohne Mailserver geht es auch.** Bleibt `SMTP_HOST` leer, erzeugt der
Scheduler weiterhin die Monatspläne und verschickt nichts; Plan und
Einkaufszettel stehen in der Weboberfläche. Dann ist `ESSENSPLANER_WEB_PASSWORT`
die einzige Pflichtangabe. Die SMTP-Werte lassen sich jederzeit nachtragen —
danach `docker compose up -d`, damit der Container sie liest.

Port 587 mit STARTTLS ist der Normalfall. Nutzt der Anbieter Port 465, dann
`SMTP_PORT=465`, `SMTP_SSL=true` und `SMTP_STARTTLS=false`.

Das Anmeldepasswort auslesen:

```bash
ssh benutzer@homeserver 'grep PASSWORT /opt/essensplaner/.env'
```

### 2.4 Weboberfläche

Erreichbar im Heimnetz unter **http://homeserver:8080**. Sie startet nur,
wenn `ESSENSPLANER_WEB_PASSWORT` gesetzt ist — eine offene Seite, auf der
Fremde den Essensplan umbauen, wäre schlimmer als keine Seite.

`ESSENSPLANER_WEB_UNSICHER=true` ist hier nötig und ein bewusster Kompromiss:
Ohne diesen Schalter markiert der Dienst das Sitzungs-Cookie als `Secure`, und
die Anmeldung über `http://` schlägt fehl. Der Preis ist, dass Passwort und
Cookie im LAN unverschlüsselt übertragen werden. Für einen Dienst, der das
Heimnetz nicht verlässt, ist das vertretbar; mit einer öffentlichen Domain
wäre es es nicht.

Soll das wegfallen, gehört ein Reverse Proxy mit TLS davor — Caddy mit einem
Zertifikat aus dem Tailnet (`tailscale cert`) ist dafür der kürzeste Weg, dann
`ESSENSPLANER_WEB_UNSICHER=false`.

### 2.5 Erreichbarkeit und Firewall

Auf dem Home-Server läuft `ufw` mit `deny incoming`; offen sind nur 22/tcp und
5353/udp (mDNS). **Docker umgeht `ufw`** bei veröffentlichten Ports: Es
schreibt eigene NAT-Regeln, die vor der `INPUT`-Kette greifen. Port 8080 ist
daher im LAN erreichbar, obwohl keine `ufw`-Regel ihn erlaubt.

Was den Zugriff begrenzt, ist nicht die Firewall des Home-Servers, sondern dass am
Gateway keine Portweiterleitung existiert. Wer es strenger will, schränkt
in der `DOCKER-USER`-Kette ein:

```bash
sudo iptables -I DOCKER-USER -p tcp --dport 8080 ! -s 192.168.1.0/24 -j DROP
```

(Nicht gesetzt — bewusst, solange nichts von außen hereinkommt.)

### 2.6 Volume für die Pläne

Das Volume `essensplaner_essensplaner-plans` wird von Compose angelegt und
nach `/data/plans` gemountet. Darin liegen der Monatsplan, selbst angelegte
Rezepte (`rezepte/*.yaml`), die Liste gestrichener Gerichte
(`gestrichen.json`) und `state.json`.

Ohne dieses Volume wäre nach jedem Rollout der Monatsplan verschwunden. Die
nächste Freitagsmail würde dann einen neuen Plan würfeln – mit anderen
Gerichten als die, für die schon eingekauft wurde. `state.json` verhindert,
dass ein Neustart eine zweite Mail auslöst.

Backup:

```bash
ssh benutzer@homeserver 'docker run --rm -v essensplaner_essensplaner-plans:/from -v /tmp:/to alpine tar czf /to/plans.tar.gz -C /from .'
```

```bash
scp benutzer@homeserver:/tmp/plans.tar.gz .
```

---

## 3. Zeitsteuerung

Der Container startet mit `essensplaner serve` und bringt seine Termine selbst
mit:

- **Wochenmail** jeden Freitag um die in `config.yaml` konfigurierte Uhrzeit,
  Standard 07:00 in der Zeitzone aus `TZ`.
- **Monatsplan** am vorletzten Freitag des Monats, 30 Minuten später, für den
  Folgemonat. Die Vorschau geht per Mail raus.
- **Vorschlag** per ntfy am Mittwoch 19:30, nur mit `NTFY_TOPIC`.
- **Erinnerung** am Donnerstag 18:00 (Mail).
- **Mealie-Rückfall** am Donnerstag 18:00, nur mit Mealie-Zugang und nur, wenn
  niemand auf „Zettel fertig“ getippt hat.

Jede Aufgabe läuft pro Termin höchstens einmal. Der Zustand liegt in
`/data/plans/state.json`. Ein Neustart am Freitagvormittag holt eine noch nicht
gelaufene Mail nach; ein Neustart am Abend tut das nicht mehr – das Zeitfenster
ist zwei Stunden breit.

Auf dem Home-Server ist dafür nichts weiter einzurichten. Wer die Termine lieber
auf dem Host sehen will, kann stattdessen zwei systemd-Timer anlegen, die
`docker compose exec essensplaner essensplaner send-weekly` bzw.
`generate-month` aufrufen — nötig ist das nicht, und beides zugleich ist
harmlos: `state.json` und der Überschreibschutz verhindern doppelte Mails.

---

## 4. Rollout einrichten

Vier Dinge gehören zusammen: der Tag im Tailnet, der OAuth-Client, der
eingeschränkte SSH-Schlüssel und die Werte im Repo.

### 4.1 Tailscale-ACL: Tag anlegen

In der Tailscale-Konsole unter **Access controls**. Ein neues Tailnet erlaubt
Mitgliedern standardmäßig alles; damit ein *getaggter* Knoten entstehen darf,
muss der Tag einen Eigentümer haben:

```json
{
  "tagOwners": {
    "tag:ci": ["autogroup:admin"]
  },
  "acls": [
    {"action": "accept", "src": ["autogroup:member"], "dst": ["*:*"]},
    {"action": "accept", "src": ["tag:ci"], "dst": ["<tailscale-ip-des-servers>:22"]}
  ]
}
```

Die zweite ACL-Regel ist der eigentliche Gewinn: Der CI-Knoten darf
ausschließlich Port 22 des Home-Servers erreichen, nichts anderes im Tailnet.

### 4.2 OAuth-Client

**Settings** → **OAuth clients** → **Generate OAuth client**:

- Scope **`auth_keys`**, schreibend
- Tag **`tag:ci`**

Der Client-Tag muss mit dem `tags:`-Wert im Workflow übereinstimmen, sonst
lehnt Tailscale den Beitritt ab. Das Secret wird nur einmal angezeigt.

Ein OAuth-Client ist einem dauerhaften Auth-Key vorzuziehen: Er erzeugt pro
Lauf einen kurzlebigen Schlüssel, und der Knoten verschwindet nach dem Lauf
wieder aus dem Tailnet.

### 4.3 Eingeschränkter SSH-Schlüssel auf dem Home-Server

Erzeugt wird das Paar auf dem Home-Server, damit der private Teil die Maschine nie
unverschlüsselt verlässt:

```bash
ssh benutzer@homeserver 'ssh-keygen -t ed25519 -N "" -f ~/.ssh/ci-deploy -C github-actions-essensplaner'
```

In `~/.ssh/authorized_keys` steht der öffentliche Teil mit Vorsatz:

```
restrict,command="/usr/local/bin/essensplaner-deploy" ssh-ed25519 AAAA… github-actions-essensplaner
```

- `command=` erzwingt genau diesen Befehl. Was die Gegenseite als
  SSH-Kommando sendet, wird verworfen.
- `restrict` schaltet Port-, Agent- und X11-Forwarding, PTY und `user-rc` ab.

Geprüft: Ein Aufruf mit `id; cat /etc/shadow` führte das Deploy-Skript aus und
sonst nichts; Daten durch einen `-L`-Tunnel kamen nicht durch.

Der private Schlüssel wandert ohne Zwischenstation in das GitHub-Secret:

```bash
ssh benutzer@homeserver 'cat ~/.ssh/ci-deploy' | gh secret set DEPLOY_SSH_KEY --repo <owner>/essensplaner
```

### 4.4 Werte im Repo

Alle fünf Werte gehören unter **Settings → Secrets and variables → Actions**
als **Secrets**:

| Secret | Wert |
|---|---|
| `TS_OAUTH_CLIENT_ID` | Client-ID aus 4.2 |
| `TS_OAUTH_SECRET` | Secret aus 4.2 |
| `DEPLOY_SSH_KEY` | privater Schlüssel aus 4.3 |
| `DEPLOY_SSH_ZIEL` | `benutzer@<tailscale-ip-des-servers>` |
| `DEPLOY_HOST_KEY` | `known_hosts`-Zeile des Servers |

Adresse und Host-Key sind streng genommen keine Geheimnisse. Sie liegen
trotzdem als Secret und nicht als Variable, denn **GitHub maskiert Secrets in
den Logs, Variables nicht** — und bei einem öffentlichen Repo sind die
Workflow-Logs öffentlich. Als Variable stünden Benutzername und Adresse des
Servers dort im Klartext.

Die Host-Key-Zeile liefert:

```bash
ssh benutzer@homeserver 'ssh-keyscan -t ed25519 $(tailscale ip -4)'
```

Sie festzunageln ist doppelter Boden: Tailscale authentifiziert den Knoten
schon, aber `StrictHostKeyChecking=yes` schließt aus, dass die Pipeline sich
stillschweigend mit einem anderen Host unterhält.

Die Werte interaktiv setzen, damit sie nicht in der Shell-History landen:

```bash
gh secret set TS_OAUTH_SECRET --repo <owner>/essensplaner
```

Fehlt einer der fünf Werte, überspringt der Workflow den Rollout mit einem
Hinweis in der Zusammenfassung — das Image liegt dann trotzdem in der
Registry und lässt sich von Hand ausrollen.

### 4.5 Optional: Freigabe vor dem Rollout

Der Rollout-Job läuft in der GitHub-Environment `homelab`. Wer eine
ausdrückliche Bestätigung vor jedem Deployment möchte, hinterlegt unter
**Settings** → **Environments** → `homelab` einen *Required reviewer*. Dann
wartet der Rollout, bis jemand zustimmt.

---

## 5. Erster Lauf

```bash
git push origin main
```

Dann unter *Actions* den Workflow **Deploy** beobachten. Die Zusammenfassung
des Rollout-Jobs enthält die Ausgabe des Deploy-Skripts, inklusive
`aktualisiert: <alt> -> <neu>` und am Ende `healthy`.

Von Hand lässt sich derselbe Weg jederzeit auslösen:

```bash
ssh benutzer@homeserver /usr/local/bin/essensplaner-deploy
```

Danach einmal den Mailversand prüfen, statt bis Freitag zu warten:

```bash
ssh benutzer@homeserver 'cd /opt/essensplaner && docker compose exec -T essensplaner essensplaner send-weekly --dry-run'
```

Das rendert die Mail und verschickt nichts. Sieht alles gut aus, dasselbe ohne
`--dry-run`. Und den Plan für den kommenden Monat anlegen, falls noch keiner
existiert:

```bash
ssh benutzer@homeserver 'cd /opt/essensplaner && docker compose exec -T essensplaner essensplaner generate-month'
```

---

## 6. Betrieb

### Das Deploy-Skript

`/usr/local/bin/essensplaner-deploy` auf dem Home-Server:

1. merkt sich die Image-ID,
2. `docker compose pull` und `up -d --remove-orphans`,
3. vergleicht die ID und räumt alte Schichten nur bei einer Änderung auf,
4. wartet bis zu zwei Minuten auf `healthy` und endet sonst mit Fehler.

Es ist idempotent: Hat sich nichts geändert, meldet es `unveraendert` und
rührt den laufenden Container nicht an. Protokoll im Journal:

```bash
ssh benutzer@homeserver 'journalctl -t essensplaner-deploy -n 30 --no-pager'
```

### Gericht im laufenden Monat tauschen

Über die Weboberfläche: Monat aufrufen, beim betreffenden Tag auf *Tauschen*,
ein Gericht aus der Liste wählen. Angeboten werden nur Gerichte, die dort
erlaubt sind; anschließend wird der ganze Monatsplan noch einmal geprüft.
Scheitert er, bleibt der alte stehen.

Die Änderung wirkt sofort: Einkaufsliste und Mails lesen dieselbe Datei im
Volume. Eine Woche, deren Mail schon verschickt ist, lässt sich nachträglich
ändern – die Mail von Freitag stimmt dann allerdings nicht mehr.

### Rezept ändern

Datei unter `data/recipes/` bearbeiten, committen, pushen. Die Pipeline prüft
und rollt aus. Ein bereits erzeugter Monatsplan bleibt unberührt – er liegt im
Volume. Soll er neu gewürfelt werden:

```bash
ssh benutzer@homeserver 'cd /opt/essensplaner && docker compose exec -T essensplaner essensplaner generate-month 2027-01 --force'
```

### Logs ansehen

```bash
ssh benutzer@homeserver 'cd /opt/essensplaner && docker compose logs -f --tail 50'
```

Der Scheduler schreibt beim Start seine Termine und Empfänger und
protokolliert jeden Lauf. Ein fehlgeschlagener Versand wird geloggt, beendet
den Dienst aber nicht – sonst fiele auch die nächste Mail aus. Compose
begrenzt die Logs auf 3 × 10 MB.

### Was tun, wenn die Freitagsmail nicht kommt

1. **Logs prüfen.** Steht dort ein SMTP-Fehler, stimmen Zugangsdaten, Port
   oder TLS-Einstellung nicht.
2. **Von Hand testen**: `essensplaner send-weekly --dry-run` im Container. Geht
   das durch, liegt es am Versand, nicht am Plan.
3. **Zeitzone prüfen.** Ohne `TZ` rechnet der Container in UTC, dann kommt die
   Mail im Winter um 08:00 deutscher Zeit.
4. **Zustand prüfen.** `state.json` im Volume zeigt, wann die Aufgaben zuletzt
   gelaufen sind. Steht der aktuelle Freitag dort, hat der Scheduler die Mail
   als erledigt verbucht – dann war der Versand selbst das Problem.

### Was tun, wenn der Rollout scheitert

| Symptom im Workflow | Ursache |
|---|---|
| `tailscale up` läuft in die Zeitüberschreitung | OAuth-Client fehlt, falsches Secret, oder `tag:ci` hat keinen Eigentümer in den ACLs |
| `Permission denied (publickey)` | `DEPLOY_SSH_KEY` passt nicht zur `authorized_keys` auf dem Home-Server |
| `Host key verification failed` | `DEPLOY_HOST_KEY` veraltet — nach einer Neuinstallation des Home-Servers neu setzen |
| `connect: connection timed out` | Home-Server ist aus oder nicht im Tailnet: `tailscale status` prüfen |
| `FEHLER: unhealthy` | Das neue Image startet nicht. Der alte Container ist schon ersetzt — Logs ansehen und notfalls ein älteres Tag in `.env` festnageln |

### Image-Version festnageln

`latest` zieht beim Neustart, was gerade veröffentlicht ist. Wer eine
bestimmte Version festhalten will, trägt in `/opt/essensplaner/.env` statt
`latest` den Commit-Tag ein, etwa `sha-a1b2c3d…`, oder den Digest aus der
Workflow-Zusammenfassung:

```bash
ESSENSPLANER_IMAGE=ghcr.io/<owner>/essensplaner@sha256:…
```

Danach `essensplaner-deploy`. Wichtig: Der nächste Push rollt dann nichts
Neues aus, weil `.env` ein festes Image vorgibt.

---

## 7. Auf andere Hardware umziehen

Das Image ist an nichts gebunden, was es nur auf dem Home-Server gibt.

1. Volume sichern (siehe 2.6).
2. Auf der neuen Maschine `docker-compose.yml`, den Override und `.env`
   anlegen, Letzteres mit `chmod 600`.
3. Volume zurückspielen:

   ```bash
   docker volume create essensplaner_essensplaner-plans
   ```

   ```bash
   docker run --rm -v essensplaner_essensplaner-plans:/to -v "$PWD:/from" alpine tar xzf /from/plans.tar.gz -C /to
   ```

4. Starten:

   ```bash
   docker compose up -d
   ```

Für den automatischen Rollout auf der neuen Maschine: Tailscale installieren
und anmelden, Deploy-Skript und eingeschränkten Schlüssel nach Abschnitt 4.3
anlegen, dann `DEPLOY_SSH_ZIEL`, `DEPLOY_HOST_KEY` und `DEPLOY_SSH_KEY` im Repo
aktualisieren. Die ACL-Regel aus 4.1 bekommt die neue Tailscale-Adresse.

Auf ARM-Hardware ändert sich am Image nichts – es liegt für `amd64` und
`arm64` bereit, Docker nimmt beim Ziehen die passende Fassung.

---

## Quellen

- [Tailscale: GitHub Action](https://tailscale.com/kb/1276/tailscale-github-action)
- [Tailscale: OAuth clients](https://tailscale.com/kb/1215/oauth-clients)
- [Tailscale: ACL tags](https://tailscale.com/kb/1068/acl-tags)
- [OpenSSH: `authorized_keys` options](https://man.openbsd.org/sshd.8#AUTHORIZED_KEYS_FILE_FORMAT)
- [Docker und ufw](https://docs.docker.com/engine/network/packet-filtering-firewalls/)
