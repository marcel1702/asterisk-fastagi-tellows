"""
Fast AGI service to lookup callerids in the tellows database
"""
import json
import logging
import os
import socketserver
import sys

import phonenumbers
import requests
import yaml
from asterisk.agi import AGI
import redis

_redis_port = os.environ.get("REDIS_PORT")
_redis_score_ttl = os.environ.get("REDIS_SCORE_TTL")
config = {"apikeyMd5": os.environ.get("APIKEYMD5"),
          "host": os.environ.get("HOST"),
          "port": os.environ.get("PORT"),
          "timeout": os.environ.get("TIMEOUT"),
          "redis_host": os.environ.get("REDIS_HOST", None),
          "redis_port": int(_redis_port) if _redis_port else None,
          "redis_score_ttl": int(_redis_score_ttl) if _redis_score_ttl else 86400,
          "default_country": os.environ.get("DEFAULT_COUNTRY", "DE"),
          "log_level": os.environ.get("LOG_LEVEL", "INFO"),
          }

if config["apikeyMd5"] is not None \
        and config["host"] is not None \
        and config["port"] is not None \
        and config["timeout"] is not None:
    print("Got configuration from environment", end=": ")
    print(config)
else:
    print("Loading config file config.yaml")
    try:
        with open("config.yaml", 'r') as stream:
            try:
                config = yaml.safe_load(stream)
                print("Got configuration from config.yaml", end=": ")
                print(config)
            except yaml.YAMLError as exc:
                print("Error opening config.yaml")
                print(exc)
    except FileNotFoundError:
        print("config.yaml not found and environment not set. Can't continue. Exiting.")
        sys.exit(-1)

if not config["apikeyMd5"] or not config["host"] or not config["port"] or not config["timeout"]:
    print(config)
    print("Missing config option(s). Exiting.")
    sys.exit(-1)

# Configure logging now that the config (incl. an optional log_level from the
# YAML file) is fully loaded and validated. The bootstrap messages above run
# before this point and intentionally stay as print().
logging.basicConfig(
    level=getattr(
        logging, str(config.get("log_level") or "INFO").upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


def parse_tellows_json(text):
    """Parse the Tellows response, tolerating a non-JSON warning.

    The Live Number endpoint may wrap the JSON body in a warning (e.g.
    "Partner Data not correct"), which the API appends as a suffix and may also
    prepend. raw_decode() reads the first JSON object starting at the leading
    "{" and ignores any trailing text. Returns the parsed dict, or None if no
    valid JSON object could be extracted.
    """
    start = text.find("{")
    if start == -1:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(text[start:])
        return obj
    except json.JSONDecodeError:
        return None


class FastAGI(socketserver.StreamRequestHandler):
    """
    FastAGI request handler for socketserver
    """
    # Close connections not finished in configured timeout seconds.
    timeout = int(config["timeout"])

    def handle(self):
        try:
            agi = AGI(stdin=self.rfile, stdout=self.wfile, stderr=sys.stderr)
            callerid = agi.env["agi_callerid"]
            logger.info("Checking caller: %s", callerid)
            if callerid != "anonymous":
                # Set up a Redis client and the normalized number once, so that
                # the whitelist lookup, the score cache lookup and the score
                # cache write can all share them. Both stay None when Redis is
                # disabled or the caller ID is not parsable, in which case all
                # Redis steps are skipped and we behave exactly as without Redis.
                redis_client = None
                fullnumber = None
                if config["redis_host"] and config["redis_port"]:
                    try:
                        e164 = phonenumbers.parse(
                            callerid, config.get("default_country", "DE"))
                        fullnumber = "+" + str(e164.country_code) + str(e164.national_number)
                        redis_client = redis.Redis(
                            host=config["redis_host"], port=config["redis_port"])
                    except phonenumbers.NumberParseException as exc:
                        logger.warning("Could not parse caller ID %r: %s", callerid, exc)

                # Check if the number is whitelisted in Redis, if yes => score 1.
                if redis_client is not None and fullnumber is not None:
                    try:
                        logger.info("Checking if %s is listed in Redis on %s:%s",
                                    fullnumber, config["redis_host"], config["redis_port"])
                        if redis_client.get(fullnumber):
                            logger.info("%s found in Redis. Not checking tellows!", fullnumber)
                            self.wfile.write(b"SET VARIABLE TELLOWS_SCORE 1\n")
                            return
                        logger.info("%s not whitelisted in Redis.", fullnumber)

                        # Check the score cache before hitting the Tellows API.
                        cached = redis_client.get("score:" + fullnumber)
                        if cached is not None:
                            try:
                                score = int(cached)
                                logger.info("%s score %d served from cache.",
                                            fullnumber, score)
                                self.wfile.write(b"SET VARIABLE TELLOWS_SCORE %d\n" % score)
                                return
                            except (TypeError, ValueError) as exc:
                                logger.warning("Invalid cached score for %s: %r: %s",
                                               fullnumber, cached, exc)
                    except redis.exceptions.RedisError as exc:
                        logger.error("Redis error, falling back to Tellows API: %s", exc)

                # Check if the number is in tellows:
                # https://www.tellows.de/apidoc/#api-Live_Number_API
                # Auth via apikeyMd5 GET param (see API docs: partner login as GET param is supported)
                agi_request = requests.request(
                    url="https://www.tellows.de/basic/num/%s" % callerid,
                    method="GET",
                    params={
                        "json": 1,
                        "apikeyMd5": config["apikeyMd5"],
                    },
                    timeout=int(config["timeout"]),
                )
                if agi_request.status_code == 200:
                    reply = parse_tellows_json(agi_request.text)
                    if reply is None:
                        logger.error("Invalid JSON response from Tellows")
                        return
                    tellows_data = reply.get("tellows", {})
                    logger.info(
                        "Response from tellows: Numb.: %s, Norm.Numb.: %s, Score: %s, "
                        "Searches: %s, Comments: %s",
                        tellows_data.get("number", ""),
                        tellows_data.get("normalizedNumber", ""),
                        tellows_data.get("score", ""),
                        tellows_data.get("searches", ""),
                        tellows_data.get("comments", ""),
                    )
                    score_raw = tellows_data.get("score")
                    try:
                        score = int(score_raw)
                    except (TypeError, ValueError) as exc:
                        logger.error("Invalid score value from Tellows: %r: %s", score_raw, exc)
                        return

                    # Cache the score so repeat callers don't consume API quota.
                    if redis_client is not None and fullnumber is not None:
                        try:
                            redis_client.setex(
                                "score:" + fullnumber,
                                int(config.get("redis_score_ttl", 86400)),
                                score,
                            )
                            logger.debug("Cached score %d for %s", score, fullnumber)
                        except redis.exceptions.RedisError as exc:
                            logger.error("Could not cache score in Redis: %s", exc)

                    self.wfile.write(b"SET VARIABLE TELLOWS_SCORE %d\n" % score)
                else:
                    logger.error("Tellows API error: %s", agi_request.status_code)
        except TypeError as exception:
            logger.error("Unable to connect to agi://%s %s",
                         self.client_address[0], exception)
        except socketserver.socket.timeout:
            logger.error("Timeout receiving data from %s", self.client_address)
        except socketserver.socket.error:
            logger.error("Could not open the socket. "
                         "Is something else listening on this port?")
        except requests.exceptions.RequestException as exception:
            logger.error("Tellows API request failed: %s", exception)


if __name__ == "__main__":
    # Connecting to API
    # https://www.tellows.de/apidoc/#api-Account-GetPartnerInfo
    request = requests.request(url="https://www.tellows.de/api/getpartnerinfo",
                               method="GET",
                               headers={
                                   "X-Auth-Token": config["apikeyMd5"]
                               },
                               timeout=int(config["timeout"]))
    if request.status_code == 200:
        partnerinfo = request.json()["partnerinfo"]
        info = str(partnerinfo.get("info", ""))
        if "company" in partnerinfo:
            info += " | Company: " + str(partnerinfo["company"])
        info += " | Allowscorelist: " + str(partnerinfo.get("allowscorelist"))
        info += " | Premium: " + str(partnerinfo.get("premium"))
        info += " | Valid until: " + str(partnerinfo.get("validuntil"))
        info += " | Requests: " + str(partnerinfo.get("requests"))
        logger.info("Successfully connected to tellows-api: %s", info)
    else:
        logger.error("Error connecting to tellows-api: %s %s, %s",
                     request.status_code,
                     request.json().get("error"),
                     request.json().get("message"))
        sys.exit(-2)

    # Create socketServer
    server = socketserver.ForkingTCPServer((config["host"], int(config["port"])), FastAGI)
    logger.info("Starting FastAGI server on %s:%s", config["host"], config["port"])
    # Keep server running until CTRL-C is pressed.
    server.serve_forever()
