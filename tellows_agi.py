"""
Fast AGI service to lookup callerids in the tellows database
"""
import datetime
import json
import os
import socketserver
import sys

import phonenumbers
import requests
import yaml
from asterisk.agi import AGI
import redis

_redis_port = os.environ.get("REDIS_PORT")
config = {"apikeyMd5": os.environ.get("APIKEYMD5"),
          "host": os.environ.get("HOST"),
          "port": os.environ.get("PORT"),
          "timeout": os.environ.get("TIMEOUT"),
          "redis_host": os.environ.get("REDIS_HOST", None),
          "redis_port": int(_redis_port) if _redis_port else None,
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
            print(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), end=": ")
            print(f"Checking caller: {callerid}")
            if callerid != "anonymous":
                # Check if the number is in redis, if yes => whitelisted
                if config["redis_host"] and config["redis_port"]:
                    try:
                        e164 = phonenumbers.parse(callerid, "DE")
                        fullnumber = "+" + str(e164.country_code) + str(e164.national_number)
                        print(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), end=": ")
                        print(f"Checking if {fullnumber} is listed in Redis "
                              f"on {config['redis_host']}:{config['redis_port']}")
                        try:
                            redis_client = redis.Redis(
                                host=config["redis_host"], port=config["redis_port"])
                            result = redis_client.get(fullnumber)
                            if result:
                                print(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), end=": ")
                                print(f"{fullnumber} found in Redis. Not checking tellows!")
                                self.wfile.write(b"SET VARIABLE TELLOWS_SCORE 1\n")
                                return
                            else:
                                print(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), end=": ")
                                print(f"{fullnumber} not found in Redis. Checking tellows!")
                        except redis.exceptions.RedisError as exc:
                            sys.stderr.write(
                                f"Redis error, falling back to Tellows API: {exc}\n")
                    except phonenumbers.NumberParseException as exc:
                        sys.stderr.write(f"Could not parse caller ID {callerid!r}: {exc}\n")

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
                    try:
                        reply = json.loads(agi_request.text)
                    except json.JSONDecodeError as exc:
                        sys.stderr.write(f"Invalid JSON response from Tellows: {exc}\n")
                        return
                    print(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), end=": ")
                    print("Response from tellows: ", end=" ")
                    tellows_data = reply.get("tellows", {})
                    print("Numb.: " + str(tellows_data.get("number", "")), end=", ")
                    print("Norm.Numb.: " + str(tellows_data.get("normalizedNumber", "")), end=", ")
                    print("Score: " + str(tellows_data.get("score", "")), end=", ")
                    print("Searches: " + str(tellows_data.get("searches", "")), end=", ")
                    print("Comments: " + str(tellows_data.get("comments", "")), end=", ")
                    score_raw = tellows_data.get("score")
                    try:
                        score = int(score_raw)
                    except (TypeError, ValueError) as exc:
                        sys.stderr.write(
                            f"Invalid score value from Tellows: {score_raw!r}: {exc}\n")
                        return
                    self.wfile.write(b"SET VARIABLE TELLOWS_SCORE %d\n" % score)
                else:
                    sys.stderr.write(f"Tellows API error: {agi_request.status_code}\n")
        except TypeError as exception:
            sys.stderr.write('Unable to connect to agi://{} {}\n'.
                             format(self.client_address[0], str(exception)))
        except socketserver.socket.timeout:
            sys.stderr.write('Timeout receiving data from {}\n'.
                             format(self.client_address))
        except socketserver.socket.error:
            sys.stderr.write('Could not open the socket. '
                             'Is something else listening on this port?\n')
        except requests.exceptions.RequestException as exception:
            sys.stderr.write(f'Tellows API request failed: {exception}\n')


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
        print("Successfully connected to tellows-api", end=": ")
        print(partnerinfo["info"], end="")
        try:
            print(" | Company: " + str(partnerinfo["company"]), end="")
        except KeyError:
            pass
        print(" | Allowscorelist: " + str(partnerinfo.get("allowscorelist")), end="")
        print(" | Premium: " + str(partnerinfo.get("premium")), end="")
        print(" | Valid until: " + str(partnerinfo.get("validuntil")), end="")
        print(" | Requests: " + str(partnerinfo.get("requests")), end="")
        print()
    else:
        print("Error connecting to tellows-api: " + str(request.status_code)
              + " " + request.json()['error'], end=", ")
        print(request.json()["message"])
        sys.exit(-2)

    # Create socketServer
    server = socketserver.ForkingTCPServer((config["host"], int(config["port"])), FastAGI)
    print("Starting FastAGI server on " + config["host"] + ":" + str(config["port"]))
    # Keep server running until CTRL-C is pressed.
    server.serve_forever()
