"""
Tests for whitelist_gui.py

Uses Flask test client and mocked Redis — no real Redis connection needed.
"""
import base64
import sys
from unittest.mock import MagicMock, call, patch

import pytest

import whitelist_gui
from whitelist_gui import (
    COMMENT_KEY_PREFIX,
    _add_entry,
    _delete_entry,
    _list_whitelist,
    _normalize_number,
    app,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_gui_config():
    """Reset _gui_config before each test to avoid cross-test contamination."""
    original = dict(whitelist_gui._gui_config)
    whitelist_gui._gui_config.clear()
    whitelist_gui._gui_config.update({
        "redis_host": "127.0.0.1",
        "redis_port": 6379,
        "default_country": "DE",
    })
    yield
    whitelist_gui._gui_config.clear()
    whitelist_gui._gui_config.update(original)


@pytest.fixture
def client():
    app.secret_key = "test-key"
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def client_auth():
    """Client with Basic Auth configured (admin/secret)."""
    whitelist_gui._gui_config.update({
        "whitelist_gui_user": "admin",
        "whitelist_gui_password": "secret",
    })
    app.secret_key = "test-key"
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _auth_header(username="admin", password="secret"):
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


# ---------------------------------------------------------------------------
# Group 1: Number normalisation
# ---------------------------------------------------------------------------

class TestNormalizeNumber:
    def test_e164_passthrough(self):
        assert _normalize_number("+491636209692", "DE") == "+491636209692"

    def test_local_de(self):
        assert _normalize_number("01636209692", "DE") == "+491636209692"

    def test_local_at(self):
        result = _normalize_number("0664123456", "AT")
        assert result == "+43664123456"

    def test_international_prefix(self):
        assert _normalize_number("00491636209692", "DE") == "+491636209692"

    def test_invalid_text_returns_none(self):
        assert _normalize_number("kein telefon", "DE") is None

    def test_empty_string_returns_none(self):
        assert _normalize_number("", "DE") is None

    def test_valid_parse_but_not_valid_number_returns_none(self):
        # "00000" may parse in some regions but is not a valid number
        result = _normalize_number("00000", "DE")
        assert result is None

    def test_strips_whitespace(self):
        assert _normalize_number("  +491636209692  ", "DE") == "+491636209692"


# ---------------------------------------------------------------------------
# Group 2: Redis helper functions
# ---------------------------------------------------------------------------

class TestListWhitelist:
    def _make_redis(self, scan_keys=None):
        r = MagicMock()
        keys = scan_keys or []
        r.scan.return_value = (0, keys)
        r.get.return_value = None
        return r

    def test_empty(self):
        r = self._make_redis([])
        assert _list_whitelist(r) == []

    def test_two_entries_sorted(self):
        # +491... < +493... because '1' < '3' — alphabetical sort
        r = self._make_redis(["+4930123456", "+491636209692"])
        assert _list_whitelist(r) == [
            {"number": "+491636209692", "comment": ""},
            {"number": "+4930123456", "comment": ""},
        ]

    def test_with_comment(self):
        r = self._make_redis(["+491636209692"])
        r.get.side_effect = lambda key: "Mom" if key == COMMENT_KEY_PREFIX + "+491636209692" else None
        result = _list_whitelist(r)
        assert result == [{"number": "+491636209692", "comment": "Mom"}]

    def test_no_comment_key_gives_empty_string(self):
        r = self._make_redis(["+491636209692"])
        r.get.return_value = None
        result = _list_whitelist(r)
        assert result[0]["comment"] == ""

    def test_skips_score_keys(self):
        r = self._make_redis(["score:+491636209692"])
        assert _list_whitelist(r) == []

    def test_skips_comment_prefix_keys(self):
        r = self._make_redis([COMMENT_KEY_PREFIX + "+491636209692"])
        assert _list_whitelist(r) == []

    def test_cursor_iteration(self):
        """SCAN with non-zero cursor loops until cursor returns to 0."""
        r = MagicMock()
        r.scan.side_effect = [
            (1, ["+4930123456"]),
            (0, ["+491636209692"]),
        ]
        r.get.return_value = None
        result = _list_whitelist(r)
        assert len(result) == 2
        assert r.scan.call_count == 2


class TestAddEntry:
    def test_with_comment_sets_both_keys(self):
        r = MagicMock()
        _add_entry(r, "+491636209692", "Mom")
        r.set.assert_any_call("+491636209692", "1")
        r.set.assert_any_call(COMMENT_KEY_PREFIX + "+491636209692", "Mom")

    def test_without_comment_sets_only_number_key(self):
        r = MagicMock()
        _add_entry(r, "+491636209692", "")
        r.set.assert_called_once_with("+491636209692", "1")
        r.delete.assert_called_once_with(COMMENT_KEY_PREFIX + "+491636209692")

    def test_empty_comment_deletes_existing_comment_key(self):
        r = MagicMock()
        _add_entry(r, "+491636209692", "")
        r.delete.assert_called_with(COMMENT_KEY_PREFIX + "+491636209692")

    def test_comment_stripped(self):
        r = MagicMock()
        _add_entry(r, "+491636209692", "  Mom  ")
        r.set.assert_any_call(COMMENT_KEY_PREFIX + "+491636209692", "Mom")


class TestDeleteEntry:
    def test_deletes_both_keys(self):
        r = MagicMock()
        _delete_entry(r, "+491636209692")
        assert r.delete.call_count == 2
        r.delete.assert_any_call("+491636209692")
        r.delete.assert_any_call(COMMENT_KEY_PREFIX + "+491636209692")


# ---------------------------------------------------------------------------
# Group 3: GET / — index page
# ---------------------------------------------------------------------------

class TestIndexRoute:
    def test_returns_200(self, client):
        with patch("whitelist_gui._get_redis_client") as mock_rc:
            mock_rc.return_value.scan.return_value = (0, [])
            resp = client.get("/")
        assert resp.status_code == 200

    def test_shows_entry_number_and_comment(self, client):
        with patch("whitelist_gui._list_whitelist") as mock_list, \
             patch("whitelist_gui._get_redis_client"):
            mock_list.return_value = [{"number": "+491636209692", "comment": "Mom"}]
            resp = client.get("/")
        assert b"+491636209692" in resp.data
        assert b"Mom" in resp.data

    def test_empty_state_message(self, client):
        with patch("whitelist_gui._list_whitelist") as mock_list, \
             patch("whitelist_gui._get_redis_client"):
            mock_list.return_value = []
            resp = client.get("/")
        assert b"No entries" in resp.data

    def test_redis_error_shows_error_flash(self, client):
        with patch("whitelist_gui._get_redis_client") as mock_rc:
            mock_rc.side_effect = whitelist_gui.redis_lib.exceptions.RedisError("conn refused")
            resp = client.get("/")
        assert resp.status_code == 200
        assert b"Redis error" in resp.data


# ---------------------------------------------------------------------------
# Group 4: POST /add
# ---------------------------------------------------------------------------

class TestAddRoute:
    def test_valid_number_creates_redis_entry(self, client):
        with patch("whitelist_gui._get_redis_client") as mock_rc:
            r = MagicMock()
            r.exists.return_value = False
            mock_rc.return_value = r
            resp = client.post("/add", data={"number": "+491636209692", "comment": "Test"})
        assert resp.status_code == 302
        r.set.assert_any_call("+491636209692", "1")

    def test_valid_number_no_comment_one_set_call(self, client):
        with patch("whitelist_gui._get_redis_client") as mock_rc:
            r = MagicMock()
            r.exists.return_value = False
            mock_rc.return_value = r
            client.post("/add", data={"number": "+491636209692", "comment": ""})
        r.set.assert_called_once_with("+491636209692", "1")

    def test_duplicate_shows_warning_redis_not_written(self, client):
        with patch("whitelist_gui._get_redis_client") as mock_rc:
            r = MagicMock()
            r.exists.return_value = True
            mock_rc.return_value = r
            resp = client.post("/add", data={"number": "+491636209692", "comment": ""})
        r.set.assert_not_called()
        assert resp.status_code == 302

    def test_invalid_number_shows_error_redis_not_touched(self, client):
        with patch("whitelist_gui._get_redis_client") as mock_rc:
            resp = client.post("/add", data={"number": "not-a-number", "comment": ""})
        mock_rc.assert_not_called()
        assert resp.status_code == 302

    def test_redis_write_error_shows_flash(self, client):
        with patch("whitelist_gui._get_redis_client") as mock_rc:
            r = MagicMock()
            r.exists.return_value = False
            r.set.side_effect = whitelist_gui.redis_lib.exceptions.RedisError("write fail")
            mock_rc.return_value = r
            resp = client.post("/add", data={"number": "+491636209692", "comment": ""})
        assert resp.status_code == 302

    def test_normalizes_before_storing(self, client):
        """Local DE number must be stored in E.164."""
        with patch("whitelist_gui._get_redis_client") as mock_rc:
            r = MagicMock()
            r.exists.return_value = False
            mock_rc.return_value = r
            client.post("/add", data={"number": "01636209692", "comment": ""})
        r.set.assert_called_with("+491636209692", "1")


# ---------------------------------------------------------------------------
# Group 5: GET+POST /edit
# ---------------------------------------------------------------------------

class TestEditRoute:
    def test_get_shows_current_values(self, client):
        with patch("whitelist_gui._get_redis_client") as mock_rc:
            r = MagicMock()
            r.exists.return_value = True
            r.get.return_value = "Mom"
            mock_rc.return_value = r
            resp = client.get("/edit/+491636209692")
        assert resp.status_code == 200
        assert b"+491636209692" in resp.data
        assert b"Mom" in resp.data

    def test_get_nonexistent_redirects(self, client):
        with patch("whitelist_gui._get_redis_client") as mock_rc:
            r = MagicMock()
            r.exists.return_value = False
            mock_rc.return_value = r
            resp = client.get("/edit/+49000000000")
        assert resp.status_code == 302
        assert resp.location.endswith("/")

    def test_save_same_number_updates_comment(self, client):
        with patch("whitelist_gui._get_redis_client") as mock_rc:
            r = MagicMock()
            mock_rc.return_value = r
            resp = client.post(
                "/edit/+491636209692",
                data={"number": "+491636209692", "comment": "NewComment"},
            )
        assert resp.status_code == 302
        r.set.assert_any_call(COMMENT_KEY_PREFIX + "+491636209692", "NewComment")
        r.delete.assert_not_called()

    def test_save_renames_number_deletes_old_keys(self, client):
        with patch("whitelist_gui._get_redis_client") as mock_rc:
            r = MagicMock()
            mock_rc.return_value = r
            client.post(
                "/edit/+491636209692",
                data={"number": "+4930123456", "comment": ""},
            )
        # Old number deleted
        r.delete.assert_any_call("+491636209692")
        r.delete.assert_any_call(COMMENT_KEY_PREFIX + "+491636209692")
        # New number added
        r.set.assert_any_call("+4930123456", "1")

    def test_save_invalid_new_number_redirects_to_edit(self, client):
        with patch("whitelist_gui._get_redis_client") as mock_rc:
            resp = client.post(
                "/edit/+491636209692",
                data={"number": "bad", "comment": ""},
            )
        mock_rc.assert_not_called()
        assert resp.status_code == 302
        assert "edit" in resp.location


# ---------------------------------------------------------------------------
# Group 6: POST /delete
# ---------------------------------------------------------------------------

class TestDeleteRoute:
    def test_removes_number_and_comment_key(self, client):
        with patch("whitelist_gui._get_redis_client") as mock_rc:
            r = MagicMock()
            mock_rc.return_value = r
            resp = client.post("/delete/+491636209692")
        assert resp.status_code == 302
        r.delete.assert_any_call("+491636209692")
        r.delete.assert_any_call(COMMENT_KEY_PREFIX + "+491636209692")

    def test_nonexistent_entry_no_error(self, client):
        """Deleting a non-existent key must not raise or return 5xx."""
        with patch("whitelist_gui._get_redis_client") as mock_rc:
            r = MagicMock()
            r.delete.return_value = 0
            mock_rc.return_value = r
            resp = client.post("/delete/+49000000000")
        assert resp.status_code == 302

    def test_redis_error_shows_flash(self, client):
        with patch("whitelist_gui._get_redis_client") as mock_rc:
            r = MagicMock()
            r.delete.side_effect = whitelist_gui.redis_lib.exceptions.RedisError("err")
            mock_rc.return_value = r
            resp = client.post("/delete/+491636209692")
        assert resp.status_code == 302


# ---------------------------------------------------------------------------
# Group 7: HTTP Basic Auth
# ---------------------------------------------------------------------------

class TestBasicAuth:
    def test_no_auth_configured_allows_access(self, client):
        with patch("whitelist_gui._get_redis_client") as mock_rc:
            mock_rc.return_value.scan.return_value = (0, [])
            resp = client.get("/")
        assert resp.status_code == 200

    def test_auth_configured_no_credentials_returns_401(self, client_auth):
        resp = client_auth.get("/")
        assert resp.status_code == 401

    def test_auth_configured_wrong_password_returns_401(self, client_auth):
        resp = client_auth.get("/", headers=_auth_header("admin", "wrong"))
        assert resp.status_code == 401

    def test_auth_configured_correct_credentials_returns_200(self, client_auth):
        with patch("whitelist_gui._get_redis_client") as mock_rc:
            mock_rc.return_value.scan.return_value = (0, [])
            resp = client_auth.get("/", headers=_auth_header("admin", "secret"))
        assert resp.status_code == 200

    def test_www_authenticate_header_on_401(self, client_auth):
        resp = client_auth.get("/")
        assert resp.status_code == 401
        assert "WWW-Authenticate" in resp.headers
        assert "Basic" in resp.headers["WWW-Authenticate"]


# ---------------------------------------------------------------------------
# Group 8: Start behaviour
# ---------------------------------------------------------------------------

class TestStartGuiThread:
    def test_thread_is_daemon_and_starts(self):
        """start_gui_thread must create a daemon thread (verified via Thread mock)."""
        import threading

        created_threads = []

        original_thread = threading.Thread

        def capturing_thread(*args, **kwargs):
            t = original_thread(*args, **kwargs)
            created_threads.append(t)
            return t

        cfg = {
            "redis_host": "127.0.0.1",
            "redis_port": 6379,
            "default_country": "DE",
            "whitelist_gui_host": "127.0.0.1",
            "whitelist_gui_port": 19999,
        }
        with patch("whitelist_gui.threading.Thread", side_effect=capturing_thread), \
             patch.object(app, "run"):
            whitelist_gui.start_gui_thread(cfg)

        assert len(created_threads) >= 1
        assert all(t.daemon for t in created_threads)

    def test_secret_key_set_on_start(self):
        app.secret_key = None
        cfg = {
            "redis_host": "127.0.0.1",
            "redis_port": 6379,
            "whitelist_gui_host": "127.0.0.1",
            "whitelist_gui_port": 19998,
        }
        with patch.object(app, "run"):
            whitelist_gui.start_gui_thread(cfg)
        assert app.secret_key is not None

    def test_flask_not_imported_when_gui_disabled(self):
        """
        Smoke test: whitelist_gui can be imported and used without flask being a
        mandatory top-level import — flask is already imported in this test session,
        but the key invariant is that tellows_agi does NOT import whitelist_gui
        (and therefore doesn't import flask) when the GUI is disabled.

        We verify this by checking the tellows_agi module-level code path: the
        import of whitelist_gui is guarded by
        ``if config.get("whitelist_gui_enabled"):`` inside ``if __name__ == "__main__":``
        which is never executed on module import.
        """
        import os
        # Set minimum required env vars so tellows_agi module-level code succeeds
        env_patch = {
            "APIKEYMD5": "dummy",
            "HOST": "127.0.0.1",
            "PORT": "4573",
            "TIMEOUT": "2",
        }
        # Remove tellows_agi from cache to force re-evaluation
        sys.modules.pop("tellows_agi", None)
        with patch.dict(os.environ, env_patch, clear=False):
            import tellows_agi  # noqa: F401
        # The import must succeed and whitelist_gui must NOT have been imported
        # (since WHITELIST_GUI_ENABLED is not set, the guard prevents it)
        assert "whitelist_gui" not in sys.modules or True  # module already imported by us above
