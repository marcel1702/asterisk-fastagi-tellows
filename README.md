# Asterisk FastAGI Integration of Tellows Blacklist API

> **Note:** This is a community fork of
> [kettenbach-it/asterisk-fastagi-tellows](https://github.com/kettenbach-it/asterisk-fastagi-tellows).
> It fixes a startup crash (`TypeError: can only concatenate str (not "int") to str`
> on the tellows `partnerinfo` fields) that caused the container to restart endlessly,
> and modernizes the build (Python 3.12 base image, slimmed-down dependencies).
> A prebuilt image is published on the GitHub Container Registry – see below.

Fast-AGI service built with Python to use the
[Tellows Blacklist API Service](https://www.tellows.de/c/about-tellows-uk/tellows-api-partnership-program/)
within Asterisk.

It requires an API-Key which can be obtained in the [Tellows Shop.](https://shop.tellows.de/de/anrufschutz-zuhause/sperrlisten-api-key.html)

## Installation
This service was developed with the aim of running in docker.
It will also work without docker, but docker is the recommended way.

### Using docker
The prebuilt image of this fork is published on the
[GitHub Container Registry](https://github.com/marcel1702/asterisk-fastagi-tellows/pkgs/container/asterisk-fastagi-tellows):

```
ghcr.io/marcel1702/asterisk-fastagi-tellows:latest
```

The image is published as a multi-arch manifest for `linux/amd64` and
`linux/arm64`, so the same tag runs on a regular x86-64 host as well as on a
64-bit Raspberry Pi (arm64) — `docker pull` picks the matching platform
automatically.

Use [docker-compose.example.yml](docker-compose.example.yml) to run your container.
The example below is self-contained and also starts the required Redis service.
Configuration is done via environment variables:

```yaml
services:
  redis:
    image: redis:7-alpine
    container_name: redis
    restart: unless-stopped
    volumes:
      - redis_data:/data

  asterisk-fastagi-tellows:
    image: ghcr.io/marcel1702/asterisk-fastagi-tellows:latest
    container_name: asterisk-fastagi-tellows
    restart: unless-stopped
    depends_on:
      - redis
    environment:
      APIKEYMD5: "<your api key as md5 hash>"
      HOST: "0.0.0.0"        # Listen on all interfaces
      PORT: 4573             # Asterisk AGI port
      TIMEOUT: 2             # Timeout in seconds
      DEFAULT_COUNTRY: DE    # Default region for caller-ID normalization (optional)
      LOG_LEVEL: INFO        # DEBUG, INFO, WARNING or ERROR (optional)
      REDIS_HOST: redis      # Name of the redis service
      REDIS_PORT: 6379
      REDIS_SCORE_TTL: 86400 # Cache looked-up scores for 24h (optional)
    ports:
      - "4573:4573"

volumes:
  redis_data:
```

### Configuration options

| Env var / YAML key                | Default     | Description                                                                 |
|-----------------------------------|-------------|-----------------------------------------------------------------------------|
| `APIKEYMD5` / `apikeyMd5`         | *(required)*| Tellows API key as MD5 hash.                                                |
| `HOST` / `host`                   | *(required)*| Listen address (`0.0.0.0` for all interfaces).                              |
| `PORT` / `port`                   | *(required)*| Listen port (Asterisk FastAGI default `4573`).                              |
| `TIMEOUT` / `timeout`             | *(required)*| Request/connection timeout in seconds.                                      |
| `DEFAULT_COUNTRY` / `default_country` | `DE`    | Default region (ISO 3166-1 alpha-2) for normalizing caller IDs to E.164.    |
| `LOG_LEVEL` / `log_level`         | `INFO`      | Log verbosity: `DEBUG`, `INFO`, `WARNING` or `ERROR`.                       |
| `REDIS_HOST` / `redis_host`       | *(empty)*   | Redis host. Leave empty to disable Redis (whitelist **and** score cache).   |
| `REDIS_PORT` / `redis_port`       | `6379`      | Redis port.                                                                 |
| `REDIS_SCORE_TTL` / `redis_score_ttl` | `86400` | Seconds a looked-up Tellows score is cached in Redis (only when Redis is enabled). |

**Redis whitelist:** store a number under its E.164 key (e.g. `+491636209692`)
to always return score `1` (trusted) without querying Tellows.

**Redis score cache:** after a successful API lookup the resulting score is
cached under `score:<E.164>` for `REDIS_SCORE_TTL` seconds, so repeat callers
no longer consume API quota. When Redis is disabled, every call queries the
Tellows API exactly as before.

### Not using docker
If not all of the four environment variables are supplied, the service will
fall back to reading the file "config.yaml" - see [config.example.yaml](config.example.yaml).

So if you want to check out the code from git and run it with python,
create a virtual env to run the code. The image runs on Python 3.12;
the code also works on older 3.x versions (3.7+). It won't work with Python 2.

Here is an example of how this is done:

```
git clone https://github.com/marcel1702/asterisk-fastagi-tellows
cd asterisk-fastagi-tellows
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml
# now edit config.yaml according to your needs
python3 tellows_agi.py
```

## Usage in Asterisk
Here's an example how you can use this FastAGI service in Asterisk
(in a Macro) assuming you deployed it to the same host Asterisk is running
at. You can deploy it to any other docker host having internet access
reachable by your asterisk host - just adjust the hostname accordingly.
Calls will be checked using the Tellows service and calls with score > 6
will be sent to the "blacklistedtellows" priority and then handled by "Zapateller"
```
; Tellows check via FastAGI
exten => s,n,AGI(agi://localhost/)
exten => s,n,GotoIf($[ ${TELLOWS_SCORE} > 6 ]?blacklistedtellows)

....

; Blacklist Tellows
exten => s,n(blacklistedtellows),Set(CHANNEL(accountcode)=blacklisted-tellows)
exten => s,n(blacklistedtellows),Zapateller(answer)
exten => s,n(blacklistedtellows),Congestion()

```

## What's different in this fork
- **Fixed:** startup `TypeError` on the `partnerinfo` fields (`allowscorelist`,
  `premium`, `validuntil`, `requests`), which the tellows API now returns as
  numbers instead of strings. This crashed the service before the FastAGI
  server could start, causing an endless container restart loop.
- **Fixed:** several crash conditions in the request handler that could take
  down a worker on malformed input — an unset `REDIS_PORT` at startup,
  unparseable caller IDs, Redis being unreachable, non-JSON/error responses
  from the API, and missing or non-numeric score fields. Network requests now
  use a timeout so a slow API can no longer hang the handler indefinitely.
- **Changed:** base image upgraded from the end-of-life `python:3.7-slim`
  to `python:3.12-slim`.
- **Changed:** `requirements.txt` reduced to the dependencies actually used at
  runtime, so the image builds reliably again.
- **Changed:** the entry point was renamed from `tellows.agi.py` to
  `tellows_agi.py` so it can be imported by the test suite.
- **Added:** automated image build & publish to ghcr.io via GitHub Actions.
- **Added:** multi-arch images (`linux/amd64` and `linux/arm64`), so the same
  tag runs on x86-64 hosts and on a 64-bit Raspberry Pi.
- **Added:** a CI workflow that runs the unit tests and a Docker build on every
  push; the publish workflow now only runs on version tags (`v*.*.*`).
- **Added:** a pytest unit-test suite (`tests/`) covering the handler logic and
  the fixed crash conditions as regression tests.
- **Added:** a Redis score cache — after a successful API lookup the score is
  cached under `score:<E.164>` for `REDIS_SCORE_TTL` seconds (default 24h), so
  repeat callers no longer consume Tellows API quota. Skipped when Redis is off.
- **Added:** structured logging via Python's `logging` module with a
  configurable `LOG_LEVEL` (`DEBUG`/`INFO`/`WARNING`/`ERROR`), replacing the
  ad-hoc `print`/`stderr` output.
- **Added:** a configurable `DEFAULT_COUNTRY` for caller-ID normalization, so
  the service works for non-German deployments (was hard-coded to `DE`).

## References

### Source Code (this fork)
[github.com/marcel1702/asterisk-fastagi-tellows](https://github.com/marcel1702/asterisk-fastagi-tellows)

### Original project
[github.com/kettenbach-it/asterisk-fastagi-tellows](https://github.com/kettenbach-it/asterisk-fastagi-tellows)
by Volker Kettenbach.

### Tellows API Documentation
<https://www.tellows.de/apidoc> (Username: tellowskey, Password: \<your_api_key\>)

## License
GNU AGPL v3 (unchanged from the original project).

For more, see [LICENSE](LICENSE)
