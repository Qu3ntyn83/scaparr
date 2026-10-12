FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 DATA_DIR=/data TZ=Europe/Berlin
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg tzdata \
    && rm -rf /var/lib/apt/lists/*

# Deno = JavaScript-Laufzeit, die yt-dlp für YouTube-Downloads braucht (sonst HTTP 403 / fehlende Formate)
COPY --from=denoland/deno:bin-2.9.6 /deno /usr/local/bin/deno

WORKDIR /opt/scraparr
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app

# PUID/PGID wie beim NAS-Share, damit erzeugte Dateien dem richtigen User gehören
ARG PUID=1000
ARG PGID=1000
# Gruppe nur anlegen, wenn die GID noch frei ist (GID 100 = "users" existiert bereits, typisch für Synology)
RUN (getent group ${PGID} >/dev/null || groupadd -g ${PGID} media) \
    && (getent passwd ${PUID} >/dev/null || useradd -u ${PUID} -g ${PGID} -M -d /data -s /usr/sbin/nologin media) \
    && mkdir -p /data && chown ${PUID}:${PGID} /data
USER ${PUID}:${PGID}

EXPOSE 8585
VOLUME ["/data"]
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:8585/api/status')" || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8585", "--proxy-headers"]
