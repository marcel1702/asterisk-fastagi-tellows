# Asterisk FastAGI Integration of Tellows Blacklist API

> **Hinweis / Note:** This is a community fork of
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
      REDIS_HOST: redis      # Name of the redis service
      REDIS_PORT: 6379
    ports:
      - "4573:4573"

volumes:
  redis_data:
```

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
python3 tellows.agi.py
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
- **Changed:** base image upgraded from the end-of-life `python:3.7-slim`
  to `python:3.12-slim`.
- **Changed:** `requirements.txt` reduced to the dependencies actually used at
  runtime, so the image builds reliably again.
- **Added:** automated image build & publish to ghcr.io via GitHub Actions.

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