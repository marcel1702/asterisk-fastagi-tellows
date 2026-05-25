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

# -u = unbuffered - sonst erscheinen keine Logs
CMD [ "python", "-u", "tellows_agi.py" ]
