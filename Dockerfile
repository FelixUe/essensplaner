# Essensplaner – Container für Coolify auf dem Hetzner-Server
# und später unverändert für die Homelab-Hardware.
#
# Zwei Stufen: die erste baut das Wheel samt Abhängigkeiten in ein venv,
# die zweite enthält nur Laufzeit. Das hält das Image klein und sorgt
# dafür, dass kein Compiler im laufenden Container liegt.

# ---------------------------------------------------------------- Build
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Erst die Metadaten kopieren: solange pyproject.toml sich nicht ändert,
# bleibt die Abhängigkeitsschicht im Cache.
COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip setuptools wheel \
    && /opt/venv/bin/pip install .

# ------------------------------------------------------------- Runtime
FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="Essensplaner" \
      org.opencontainers.image.description="Monatsessensplan mit wöchentlicher Einkaufsliste per Mail" \
      org.opencontainers.image.source="https://github.com/FelixUe/essensplaner"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    TZ=Europe/Berlin \
    ESSENSPLANER_CONFIG=/app/config.yaml \
    ESSENSPLANER_DATA_DIR=/app/data \
    ESSENSPLANER_PLANS_DIR=/data/plans

# curl für den Healthcheck-freien Betrieb nicht nötig – bewusst kein
# zusätzliches Paket, das Image bleibt bei den Python-Grundlagen.
COPY --from=builder /opt/venv /opt/venv

WORKDIR /app

# Konfiguration, Rezepte und Zutaten liegen im Image und kommen aus dem
# Repo. Wer ein Rezept ändert, committet – die Pipeline rollt es aus.
COPY config.yaml ./config.yaml
COPY data ./data
COPY tools ./tools

# Die erzeugten Pläne sind Laufzeitdaten und gehören in ein Volume,
# damit sie einen Rollout überleben.
RUN useradd --create-home --uid 10001 essensplaner \
    && mkdir -p /data/plans \
    && chown -R essensplaner:essensplaner /app /data

VOLUME ["/data/plans"]

USER essensplaner

# Der Healthcheck prüft die Stammdaten. Schlägt er an, stimmt etwas mit
# den Rezepten oder Zutaten nicht – das sieht man in Coolify sofort,
# statt es erst in der Freitagsmail zu merken.
HEALTHCHECK --interval=5m --timeout=30s --start-period=20s --retries=2 \
    CMD ["essensplaner", "validate", "--no-protein"]

# Standardbetrieb: eingebauter Scheduler. Wer in Coolify lieber zwei
# Scheduled Tasks nutzt, überschreibt das Kommando und ruft
# "essensplaner send-weekly" bzw. "essensplaner generate-month" auf.
ENTRYPOINT ["essensplaner"]
CMD ["serve"]
