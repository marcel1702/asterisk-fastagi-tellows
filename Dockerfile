FROM python:3.12-slim

LABEL maintainer="marcel1702"

# Zeitzone fuer die Log-Timestamps (Original nutzte Europe/Berlin)
ENV TZ=Europe/Berlin

WORKDIR /srv

COPY requirements.txt ./

RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install --no-cache-dir --upgrade pip \
    && pip3 install --no-cache-dir -r requirements.txt

COPY . .

# Ports the container listens on:
#   4573 = Asterisk FastAGI
#   8080 = optional whitelist management GUI (only when WHITELIST_GUI_ENABLED=true)
EXPOSE 4573 8080

# Mark the container unhealthy when the FastAGI port stops accepting connections.
# Such probe connections are ignored by the server and not logged (except at DEBUG).
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD [ "python", "healthcheck.py" ]

# -u = unbuffered - sonst erscheinen keine Logs
CMD [ "python", "-u", "tellows_agi.py" ]
