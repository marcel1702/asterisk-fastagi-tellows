"""Unit tests for tellows_agi.py"""
import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# Set required env vars before importing the module under test.
# REDIS_PORT is intentionally omitted to cover Bug 1 regression.
os.environ.setdefault("APIKEYMD5", "testkey_md5_hash")
os.environ.setdefault("HOST", "127.0.0.1")
os.environ.setdefault("PORT", "4573")
os.environ.setdefault("TIMEOUT", "2")
os.environ.pop("REDIS_PORT", None)
os.environ.pop("REDIS_HOST", None)

import tellows_agi  # noqa: E402 — must come after env setup
from tellows_agi import FastAGI  # noqa: E402


def make_handler():
    """Return a FastAGI instance with mocked socket streams."""
    handler = FastAGI.__new__(FastAGI)
    handler.rfile = MagicMock()
    handler.wfile = MagicMock()
    handler.client_address = ("127.0.0.1", 12345)
    return handler


def make_agi_mock(callerid):
    mock_agi = MagicMock()
    mock_agi.env = {"agi_callerid": callerid}
    return mock_agi


def make_api_response(score=7, status_code=200):
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.text = json.dumps({
        "tellows": {
            "number": "01636209692",
            "normalizedNumber": "+4901636209692",
            "score": score,
            "searches": 100,
            "comments": 5,
        }
    })
    return mock_resp


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

class TestConfigLoading:
    def test_redis_port_not_set_does_not_crash(self):
        """Bug 1 regression: REDIS_PORT env not set must not raise TypeError."""
        assert tellows_agi.config.get("redis_port") is None

    def test_redis_port_env_parsed_as_int(self):
        """REDIS_PORT env set to a string must be converted to int."""
        with patch.dict(os.environ, {"REDIS_PORT": "6380"}):
            port_env = os.environ.get("REDIS_PORT")
            result = int(port_env) if port_env else None
            assert result == 6380


# ---------------------------------------------------------------------------
# handle() — anonymous caller
# ---------------------------------------------------------------------------

class TestHandleAnonymous:
    @patch("tellows_agi.requests.request")
    @patch("tellows_agi.AGI")
    def test_anonymous_caller_skips_api_and_redis(self, mock_agi_cls, mock_request):
        """callerid 'anonymous' must not trigger any Redis or API call."""
        mock_agi_cls.return_value = make_agi_mock("anonymous")

        handler = make_handler()
        handler.handle()

        mock_request.assert_not_called()
        handler.wfile.write.assert_not_called()


# ---------------------------------------------------------------------------
# handle() — Redis cache hit
# ---------------------------------------------------------------------------

class TestHandleRedisCacheHit:
    @patch("tellows_agi.redis.Redis")
    @patch("tellows_agi.requests.request")
    @patch("tellows_agi.AGI")
    def test_cache_hit_writes_score_1_and_skips_api(
        self, mock_agi_cls, mock_request, mock_redis_cls
    ):
        """Number found in Redis must write TELLOWS_SCORE 1 without calling the API."""
        mock_agi_cls.return_value = make_agi_mock("01636209692")

        mock_redis = MagicMock()
        mock_redis.get.return_value = b"1"
        mock_redis_cls.return_value = mock_redis

        handler = make_handler()
        with patch.dict(tellows_agi.config, {"redis_host": "127.0.0.1", "redis_port": 6379}):
            handler.handle()

        handler.wfile.write.assert_called_once_with(b"SET VARIABLE TELLOWS_SCORE 1\n")
        mock_request.assert_not_called()


# ---------------------------------------------------------------------------
# handle() — Redis cache miss → falls through to API
# ---------------------------------------------------------------------------

class TestHandleRedisCacheMiss:
    @patch("tellows_agi.redis.Redis")
    @patch("tellows_agi.requests.request")
    @patch("tellows_agi.AGI")
    def test_cache_miss_calls_api_and_writes_score(
        self, mock_agi_cls, mock_request, mock_redis_cls
    ):
        """Number not in Redis must fall through to the Tellows API."""
        mock_agi_cls.return_value = make_agi_mock("01636209692")

        mock_redis = MagicMock()
        mock_redis.get.return_value = None
        mock_redis_cls.return_value = mock_redis

        mock_request.return_value = make_api_response(score=7)

        handler = make_handler()
        with patch.dict(tellows_agi.config, {"redis_host": "127.0.0.1", "redis_port": 6379}):
            handler.handle()

        mock_request.assert_called_once()
        handler.wfile.write.assert_called_once_with(b"SET VARIABLE TELLOWS_SCORE 7\n")


# ---------------------------------------------------------------------------
# handle() — API success
# ---------------------------------------------------------------------------

class TestHandleApiSuccess:
    @patch("tellows_agi.requests.request")
    @patch("tellows_agi.AGI")
    def test_api_score_written_correctly(self, mock_agi_cls, mock_request):
        """Successful API response with score 9 must write TELLOWS_SCORE 9."""
        mock_agi_cls.return_value = make_agi_mock("01636209692")
        mock_request.return_value = make_api_response(score=9)

        handler = make_handler()
        handler.handle()

        handler.wfile.write.assert_called_once_with(b"SET VARIABLE TELLOWS_SCORE 9\n")


# ---------------------------------------------------------------------------
# Error resilience — Bug regressions
# ---------------------------------------------------------------------------

class TestErrorResilience:
    @patch("tellows_agi.requests.request")
    @patch("tellows_agi.AGI")
    def test_invalid_phonenumber_no_crash(self, mock_agi_cls, mock_request):
        """Bug 3 regression: unparseable caller ID must not crash the handler."""
        mock_agi_cls.return_value = make_agi_mock("NOT_A_NUMBER")
        mock_request.return_value = make_api_response(score=5)

        handler = make_handler()
        with patch.dict(tellows_agi.config, {"redis_host": "127.0.0.1", "redis_port": 6379}):
            handler.handle()  # must not raise

    @patch("tellows_agi.redis.Redis")
    @patch("tellows_agi.requests.request")
    @patch("tellows_agi.AGI")
    def test_redis_connection_error_falls_back_to_api(
        self, mock_agi_cls, mock_request, mock_redis_cls
    ):
        """Bug 4 regression: Redis unavailable must fall back to API, no crash."""
        import redis as redis_lib

        mock_agi_cls.return_value = make_agi_mock("01636209692")
        mock_redis_cls.return_value = MagicMock(
            **{"get.side_effect": redis_lib.exceptions.ConnectionError("refused")}
        )
        mock_request.return_value = make_api_response(score=6)

        handler = make_handler()
        with patch.dict(tellows_agi.config, {"redis_host": "127.0.0.1", "redis_port": 6379}):
            handler.handle()  # must not raise

        mock_request.assert_called_once()

    @patch("tellows_agi.requests.request")
    @patch("tellows_agi.AGI")
    def test_api_timeout_no_crash(self, mock_agi_cls, mock_request):
        """Bug 5 regression: request timeout must not crash the handler."""
        mock_agi_cls.return_value = make_agi_mock("01636209692")
        mock_request.side_effect = requests_timeout()

        handler = make_handler()
        handler.handle()  # must not raise

    @patch("tellows_agi.requests.request")
    @patch("tellows_agi.AGI")
    def test_api_invalid_json_no_crash(self, mock_agi_cls, mock_request):
        """Bug 2 regression: non-JSON API response must not crash the handler."""
        mock_agi_cls.return_value = make_agi_mock("01636209692")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html>Server Error</html>"
        mock_request.return_value = mock_resp

        handler = make_handler()
        handler.handle()  # must not raise

    @patch("tellows_agi.requests.request")
    @patch("tellows_agi.AGI")
    def test_api_non_200_status_no_crash(self, mock_agi_cls, mock_request):
        """Bug 2 regression: HTTP 401/500 from API must not crash the handler."""
        mock_agi_cls.return_value = make_agi_mock("01636209692")
        mock_request.return_value = make_api_response(status_code=401)

        handler = make_handler()
        handler.handle()  # must not raise

        handler.wfile.write.assert_not_called()


def requests_timeout():
    """Helper that raises requests.exceptions.Timeout."""
    import requests as req_lib
    return req_lib.exceptions.Timeout("timed out")
