# Asterisk FastAGI Integration of Tellows Blacklist API

> **Note:** This is a community fork of
> [kettenbach-it/asterisk-fastagi-tellows](https://github.com/kettenbach-it/asterisk-fastagi-tellows).
> It fixes the startup crash that made the original container restart endlessly,
> modernizes the build (Python 3.12 base image, slimmed-down dependencies) and
> extends the project with additional features — a Redis score cache, an optional
> whitelist management web GUI, structured logging and a configurable default
> country. A prebuilt image is published on Docker Hub (with GitHub Container
> Registry as a fallback) – see below.
> The full list of changes is in [What's different in this fork](#whats-different-in-this-fork).

Fast-AGI service built with Python to use the
[Tellows Blacklist API Service](https://www.tellows.de/c/about-tellows-uk/tellows-api-partnership-program/)
within Asterisk.

It requires an API-Key which can be obtained in the [Tellows Shop.](https://shop.tellows.de/de/anrufschutz-zuhause/sperrlisten-api-key.html)

## Installation
This service was developed with the aim of running in docker.
It will also work without docker, but docker is the recommended way.

### Using docker
The prebuilt image of this fork is published on
[Docker Hub](https://hub.docker.com/r/mofis/asterisk-fastagi-tellows):

```
mofis/asterisk-fastagi-tellows:latest
```

The image is published as a multi-arch manifest for `linux/amd64` and
`linux/arm64`, so the same tag runs on a regular x86-64 host as well as on a
64-bit Raspberry Pi (arm64) — `docker pull` picks the matching platform
automatically.

The same image is also mirrored to the
[GitHub Container Registry](https://github.com/marcel1702/asterisk-fastagi-tellows/pkgs/container/asterisk-fastagi-tellows)
as a fallback (`ghcr.io/marcel1702/asterisk-fastagi-tellows:latest`).

Use [docker-compose.example.yml](https://github.com/marcel1702/asterisk-fastagi-tellows/blob/main/docker-compose.example.yml) to run your container.
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
    image: mofis/asterisk-fastagi-tellows:latest
    # Fallback: ghcr.io/marcel1702/asterisk-fastagi-tellows:latest
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
      WHITELIST_GUI_ENABLED: "false"   # Set to "true" to enable the web GUI (optional)
      WHITELIST_GUI_HOST: "0.0.0.0"   # Inside Docker bind to all interfaces
      WHITELIST_GUI_PORT: 8080
      # WHITELIST_GUI_USER: "admin"    # When set: enables HTTP Basic Auth
      # WHITELIST_GUI_PASSWORD: "secret"
    ports:
      - "4573:4573"
      # - "127.0.0.1:8080:8080"       # Whitelist GUI – only expose behind a reverse proxy

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
| `WHITELIST_GUI_ENABLED` / `whitelist_gui_enabled` | `false` | Enable the optional whitelist management web GUI (requires Redis). |
| `WHITELIST_GUI_HOST` / `whitelist_gui_host` | `127.0.0.1` | GUI bind address. Use `0.0.0.0` inside Docker. |
| `WHITELIST_GUI_PORT` / `whitelist_gui_port` | `8080` | GUI HTTP port. |
| `WHITELIST_GUI_USER` / `whitelist_gui_user` | *(empty)* | Username for the GUI's HTTP Basic Auth (set both user and password to enable it). |
| `WHITELIST_GUI_PASSWORD` / `whitelist_gui_password` | *(empty)* | Password for the GUI's HTTP Basic Auth. |

**Redis whitelist:** store a number under its E.164 key (e.g. `+491636209692`)
to always return score `1` (trusted) without querying Tellows.

**Redis score cache:** after a successful API lookup the resulting score is
cached under `score:<E.164>` for `REDIS_SCORE_TTL` seconds, so repeat callers
no longer consume API quota. When Redis is disabled, every call queries the
Tellows API exactly as before.

### Whitelist management GUI

An optional browser-based GUI lets you add, edit, and delete Redis whitelist entries
without using `redis-cli`. It is **disabled by default**; enable it with
`WHITELIST_GUI_ENABLED=true` (all GUI settings are listed in the configuration
table above).

**Requires** `REDIS_HOST` to be configured — the GUI is silently disabled if Redis is off.

The GUI runs on the [waitress](https://github.com/Pylons/waitress) production WSGI
server in a background thread, so it adds no overhead to the FastAGI handler and
does not block call processing.

**Security notes:**
- The GUI binds to `127.0.0.1` by default (loopback only).
- Set `WHITELIST_GUI_USER` **and** `WHITELIST_GUI_PASSWORD` to enable HTTP Basic Auth. When either is unset, no login is required.
- For HTTPS and stricter access control, place nginx or Caddy in front of the GUI. Inside Docker, set `WHITELIST_GUI_HOST=0.0.0.0` and only expose the port on a loopback or internal interface on the host (see the commented-out port mapping in `docker-compose.example.yml`).

**Number format:** the GUI accepts both E.164 (`+491636209692`) and local numbers (`01636209692`). Numbers are validated and normalized to E.164 before being stored.

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
- **Added:** automated image build & publish to Docker Hub (and ghcr.io as a
  fallback) via GitHub Actions; the Docker Hub description is kept in sync with
  this README automatically.
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
- **Added:** an optional whitelist management web GUI (`WHITELIST_GUI_ENABLED`) —
  a small Flask app to add, edit and delete Redis whitelist entries with comments
  and E.164 number validation, protected by optional HTTP Basic Auth. Disabled by
  default and imported only when enabled, so deployments that don't use it are
  unaffected. See [Whitelist management GUI](#whitelist-management-gui).

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
