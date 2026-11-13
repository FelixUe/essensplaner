# Deployment

Ziel: Der Essensplaner läuft als Container unter Coolify auf dem
Hetzner-Server, und jeder Push auf `main` rollt ihn automatisch aus. Später
zieht derselbe Container ohne Änderung auf die Homelab-Hardware.

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
   └─ Rollout        GET auf die Coolify-Deploy-URL
                     Coolify zieht das Image und startet den Container neu
```

Der Rollout hängt an `needs: image`, und das Image hängt an den Prüfungen. Ein
Rezept mit Fenchel oder ein Montagsgericht unter 30 g Protein lässt die
Pipeline rot werden, bevor irgendetwas den Server erreicht.

### Architekturen

Die Pipeline baut jedes Image für `linux/amd64` **und** `linux/arm64`. Unter
einem Tag wie `latest` liegt deshalb keine einzelne Datei, sondern eine Liste,
aus der Docker beim Ziehen die passende Fassung nimmt. Derselbe Tag läuft
damit auf einem x86-Server, auf Hetzners ARM-Reihe (CAX) und auf einem
Raspberry Pi mit 64-Bit-System – ohne dass irgendwo etwas umzustellen wäre.

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

## 2. Coolify einrichten

### 2.1 Ressource anlegen

In Coolify auf dem Hetzner-Server:

1. Projekt auswählen oder neu anlegen, dann **+ New Resource**.
2. **Docker Image** wählen.
3. Als Image eintragen:

   ```
   ghcr.io/<owner>/essensplaner:latest
   ```

   `<owner>` ist der GitHub-Account, komplett in Kleinbuchstaben – die
   GitHub-Registry akzeptiert keine Großbuchstaben.

Alternativ lässt sich die mitgelieferte `docker-compose.yml` als
**Docker Compose**-Ressource verwenden. Dann übernimmt Coolify Volume und
Healthcheck direkt aus der Datei.

### 2.2 Privates Image freischalten

Ein neues GitHub-Paket ist zunächst privat. Zwei Wege:

- **Paket öffentlich machen**: Auf GitHub unter *Packages* das Paket öffnen,
  *Package settings* → *Change visibility* → *Public*. Einfachster Weg, und bei
  diesem Repo unkritisch, weil das Image keine Zugangsdaten enthält – die
  kommen erst zur Laufzeit aus der Umgebung. Das Image enthält allerdings die
  Rezepte und die Haushaltskonfiguration.
- **Paket privat lassen** und Coolify anmelden: unter *Settings* → *Docker
  Registries* einen Eintrag für `ghcr.io` mit GitHub-Benutzernamen und einem
  Personal Access Token mit `read:packages` hinterlegen. Coolify führt damit
  `docker login` auf dem Server aus.

Bleibt das Paket privat, ist der zweite Weg nötig – sonst scheitert der Pull.

### 2.3 Volume für die Pläne

**Storage** → **+ Add**:

| Feld | Wert |
|---|---|
| Name | `essensplaner-plans` |
| Destination Path | `/data/plans` |

Ohne dieses Volume wäre nach jedem Rollout der Monatsplan verschwunden. Die
nächste Freitagsmail würde dann einen neuen Plan würfeln – mit anderen
Gerichten als die, für die schon eingekauft wurde. Hier liegt auch
`state.json`, das verhindert, dass ein Neustart eine zweite Mail auslöst.

### 2.4 Umgebungsvariablen

Unter **Environment Variables** eintragen. Alles, was ein Passwort oder Key
ist, als **Secret** markieren.

| Variable | Beispiel | Pflicht |
|---|---|---|
| `TZ` | `Europe/Berlin` | ja |
| `SMTP_HOST` | `smtp.example.com` | nein, siehe unten |
| `SMTP_PORT` | `587` | – |
| `SMTP_USER` | `essensplaner@example.com` | meist |
| `SMTP_PASSWORD` | – | meist, **Secret** |
| `SMTP_STARTTLS` | `true` | – |
| `SMTP_SSL` | `false` | – |
| `MAIL_FROM` | `essensplaner@example.com` | nur mit Mail |
| `MAIL_TO` | `erste.person@example.com,zweite.person@example.com` | nur mit Mail |
| `ESSENSPLANER_FREEZER` | `false` | – |
| `ESSENSPLANER_SEED` | leer | – |
| `ESSENSPLANER_WEB_PASSWORT` | – | für die Oberfläche, **Secret** |
| `ESSENSPLANER_WEB_SECRET` | – | empfohlen, **Secret** |
| `ESSENSPLANER_WEB_PORT` | `8080` | – |

**Ohne Mailserver geht es auch.** Bleibt `SMTP_HOST` leer, erzeugt der
Scheduler weiterhin die Monatspläne und verschickt nichts; Plan und
Einkaufszettel stehen in der Weboberfläche. Dann ist `ESSENSPLANER_WEB_PASSWORT`
die einzige Pflichtangabe. Die SMTP-Werte lassen sich jederzeit nachtragen.

`MAIL_TO` nimmt mehrere Adressen, mit Komma getrennt. Die Werte überschreiben,
was in `config.yaml` steht – dort stehen nur Platzhalter, damit nie eine echte
Adresse im Repo landet.

Port 587 mit STARTTLS ist der Normalfall. Nutzt der Anbieter Port 465, dann
`SMTP_PORT=465`, `SMTP_SSL=true` und `SMTP_STARTTLS=false`.

### 2.5 Weboberfläche und Domain

Die Oberfläche zeigt den Monatsplan und erlaubt, einzelne Gerichte zu
tauschen. Sie lauscht im Container auf Port **8080**.

Sie startet nur, wenn `ESSENSPLANER_WEB_PASSWORT` gesetzt ist. Ohne Passwort
läuft allein der Scheduler weiter – eine offene Seite, auf der Fremde den
Essensplan umbauen, wäre schlimmer als keine Seite.

Zwei Werte gehören dazu, beide als **Secret**:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

- `ESSENSPLANER_WEB_PASSWORT` – was man beim Anmelden eintippt.
- `ESSENSPLANER_WEB_SECRET` – signiert das Sitzungs-Cookie. Ohne festen Wert
  erzeugt der Container bei jedem Start einen neuen, und alle müssen sich
  nach jedem Rollout neu anmelden.

In Coolify bei der Ressource unter **Domains** eine Domain eintragen und als
Port `8080` angeben. Coolify kümmert sich um Proxy und Zertifikat. Die
mitgelieferte `docker-compose.yml` enthält dafür bereits
`SERVICE_FQDN_ESSENSPLANER_8080`; trägt Coolify die Domain nicht von allein
ein, geht es über das Feld in der Oberfläche.

Ohne Domain bleibt die Oberfläche unerreichbar, und das ist ein gültiger
Zustand: Mails kommen trotzdem.

**Nicht ohne HTTPS betreiben.** Das Sitzungs-Cookie wird nur über
verschlüsselte Verbindungen gesendet. Wer die Oberfläche zum Ausprobieren
ohne TLS aufruft, setzt `ESSENSPLANER_WEB_UNSICHER=true` – aber nur lokal,
nie mit einer öffentlichen Domain.

---

## 3. Zeitsteuerung wählen

Es gibt zwei Wege. Beide funktionieren, der erste ist der portablere.

### Variante A: eingebauter Scheduler (Standard)

Der Container startet mit `essensplaner serve` und bringt seine Termine selbst
mit:

- **Wochenmail** jeden Freitag um die in `config.yaml` konfigurierte Uhrzeit,
  Standard 07:00 in der Zeitzone aus `TZ`.
- **Monatsplan** am vorletzten Freitag des Monats, 30 Minuten später, für den
  Folgemonat. Die Vorschau geht per Mail raus.

Jede Aufgabe läuft pro Termin höchstens einmal. Der Zustand liegt in
`/data/plans/state.json`. Ein Neustart am Freitagvormittag holt eine noch nicht
gelaufene Mail nach; ein Neustart am Abend tut das nicht mehr – das Zeitfenster
ist zwei Stunden breit.

Hier ist in Coolify nichts weiter zu tun.

### Variante B: Coolify Scheduled Tasks

Wer die Termine in Coolify sehen will: Das Kommando des Containers auf etwas
Dauerhaftes setzen, das nichts tut (z. B. `sleep infinity`), und zwei
Scheduled Tasks anlegen:

| Name | Container Command | Frequency |
|---|---|---|
| `send-weekly` | `essensplaner send-weekly` | `0 7 * * 5` |
| `generate-month` | `essensplaner generate-month` | `30 7 * * 5` |

`generate-month` ohne Monatsangabe plant immer den Folgemonat und
überschreibt einen vorhandenen Plan nicht. Es kann also jeden Freitag laufen;
Wirkung hat es nur, solange für den Folgemonat noch kein Plan existiert.

Variante A und B zugleich sind unnötig, aber harmlos – der Zustand in
`state.json` und der Überschreibschutz verhindern doppelte Mails.

---

## 4. GitHub-Secrets für den Rollout

Coolify braucht einen Anstoß, sobald ein neues Image bereitsteht.

### 4.1 API-Token in Coolify

1. In Coolify oben rechts auf das Profil → **Keys & Tokens** → **API tokens**.
2. **Create New Token**, bei den Berechtigungen **Deploy** ankreuzen.
3. Den Token kopieren – er wird nur einmal angezeigt.

Ein Token ohne `Deploy`-Berechtigung kann keinen Rollout auslösen und
antwortet mit einem Authentifizierungsfehler.

### 4.2 Deploy-URL der Ressource

Bei der Ressource unter **Webhooks** steht die Deploy-URL. Sie hat die Form:

```
https://<coolify-host>/api/v1/deploy?uuid=<resource-uuid>
```

### 4.3 Secrets im Repo setzen

Im GitHub-Repo unter **Settings** → **Secrets and variables** → **Actions**:

| Secret | Wert |
|---|---|
| `COOLIFY_WEBHOOK` | die Deploy-URL von oben |
| `COOLIFY_TOKEN` | der API-Token mit Deploy-Berechtigung |

Oder per CLI:

```bash
gh secret set COOLIFY_WEBHOOK --body "https://coolify.example.com/api/v1/deploy?uuid=abc123"
```

```bash
gh secret set COOLIFY_TOKEN
```

Der zweite Befehl fragt den Wert interaktiv ab, damit der Token nicht in der
Shell-History landet.

Fehlt eines der beiden Secrets, überspringt der Workflow den Rollout mit einem
Hinweis in der Zusammenfassung – das Image liegt dann trotzdem in der Registry
und kann in Coolify von Hand ausgerollt werden.

### 4.4 Optional: Freigabe vor dem Rollout

Der Rollout-Job läuft in der GitHub-Environment `hetzner`. Wer eine
ausdrückliche Bestätigung vor jedem Deployment möchte, hinterlegt unter
**Settings** → **Environments** → `hetzner` einen *Required reviewer*. Dann
wartet der Rollout, bis jemand zustimmt.

---

## 5. Erster Lauf

```bash
git push origin main
```

Dann unter *Actions* den Workflow **Deploy** beobachten. Nach dem Durchlauf:

1. In Coolify sollte die Ressource laufen und nach etwa einer Minute auf
   **healthy** stehen. Der Healthcheck ruft `essensplaner validate` auf –
   healthy heißt also, dass Rezepte und Zutaten in Ordnung sind.
2. Den Mailversand einmal von Hand auslösen, statt bis Freitag zu warten. In
   Coolify unter **Terminal** oder per SSH:

   ```bash
   docker exec -it <container> essensplaner send-weekly --dry-run
   ```

   Das rendert die Mail und verschickt nichts. Sieht alles gut aus, dasselbe
   ohne `--dry-run`:

   ```bash
   docker exec -it <container> essensplaner send-weekly
   ```

3. Den Plan für den kommenden Monat anlegen, falls noch keiner existiert:

   ```bash
   docker exec -it <container> essensplaner generate-month
   ```

---

## 6. Betrieb

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
docker exec -it <container> essensplaner generate-month 2027-01 --force
```

### Logs ansehen

In Coolify unter **Logs**. Der Scheduler schreibt beim Start seine Termine und
Empfänger und protokolliert jeden Lauf. Ein fehlgeschlagener Versand wird
geloggt, beendet den Dienst aber nicht – sonst fiele auch die nächste Mail aus.

### Was tun, wenn die Freitagsmail nicht kommt

1. **Logs prüfen.** Steht dort ein SMTP-Fehler, stimmen Zugangsdaten, Port
   oder TLS-Einstellung nicht.
2. **Von Hand testen**: `essensplaner send-weekly --dry-run` im Container. Geht
   das durch, liegt es am Versand, nicht am Plan.
3. **Zeitzone prüfen.** Ohne `TZ` rechnet der Container in UTC, dann kommt die
   Mail im Winter um 08:00 deutscher Zeit.
4. **Zustand prüfen.** `cat /data/plans/state.json` zeigt, wann die Aufgaben
   zuletzt gelaufen sind. Steht der aktuelle Freitag dort, hat der Scheduler
   die Mail als erledigt verbucht – dann war der Versand selbst das Problem.

### Image-Version festnageln

`latest` zieht beim Neustart, was gerade veröffentlicht ist. Wer eine
bestimmte Version festhalten will, trägt in Coolify statt `latest` den
Commit-Tag ein, etwa `sha-a1b2c3d…`, oder den Digest aus der
Workflow-Zusammenfassung.

---

## 7. Umzug ins Homelab

Das Image ist an nichts gebunden, was es nur bei Hetzner gibt.

1. Volume sichern:

   ```bash
   docker run --rm -v essensplaner-plans:/from -v "$PWD:/to" alpine tar czf /to/plans.tar.gz -C /from .
   ```

2. Auf der neuen Maschine `.env` aus `.env.example` anlegen und ausfüllen.

3. Volume zurückspielen:

   ```bash
   docker volume create essensplaner-plans
   ```

   ```bash
   docker run --rm -v essensplaner-plans:/to -v "$PWD:/from" alpine tar xzf /from/plans.tar.gz -C /to
   ```

4. Starten:

   ```bash
   docker compose up -d
   ```

Läuft dort wieder Coolify, bleibt es bei der Anleitung aus Abschnitt 2 – nur
die Deploy-URL im Secret `COOLIFY_WEBHOOK` ändert sich.

### Wechsel auf einen ARM-Server

Bei Hetzner lässt sich ein x86-Server nicht in einen ARM-Server umwandeln; die
CAX-Reihe ist eine eigene Maschine. Der Wechsel ist deshalb derselbe Weg wie
oben: Volume sichern, neuen Server aufsetzen, Coolify installieren, Ressource
nach Abschnitt 2 neu anlegen, Volume zurückspielen, neue Deploy-URL als Secret
setzen.

Am Image ändert sich nichts – es liegt ohnehin für beide Architekturen bereit.
Was sich ändert, ist die Serveradresse in `COOLIFY_WEBHOOK` und der API-Token,
denn der gehört zur Coolify-Installation, nicht zum Konto.

---

## Quellen

- [Coolify: Deploy webhooks](https://coolify.io/docs/core/automation/deploy-webhooks)
- [Coolify: GitHub Actions](https://coolify.io/docs/applications/sources/github/actions)
- [Coolify: Deploy a Docker image](https://coolify.io/docs/applications/deployments/docker-image)
- [Coolify: Docker registries](https://coolify.io/docs/knowledge-base/docker/registry)
