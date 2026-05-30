"""
Whitelist management web GUI for the Tellows FastAGI service.

Embedded usage (via tellows_agi.py when WHITELIST_GUI_ENABLED=true):
    The start_gui_thread() function is called once at startup.

Standalone usage (development / testing):
    REDIS_HOST=127.0.0.1 REDIS_PORT=6379 python whitelist_gui.py
"""
import logging
import secrets
import threading
from functools import wraps

import phonenumbers
import redis as redis_lib
from flask import Flask, Response, flash, redirect, render_template_string, request, url_for

logger = logging.getLogger(__name__)

COMMENT_KEY_PREFIX = "whitelist_comment:"

app = Flask(__name__)
app.secret_key = None
_gui_config: dict = {}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def start_gui_thread(config: dict) -> None:
    """Start the Flask GUI in a daemon thread. Returns immediately."""
    _gui_config.update(config)
    app.secret_key = secrets.token_hex(32)
    host = config.get("whitelist_gui_host", "127.0.0.1")
    port = int(config.get("whitelist_gui_port", 8080))
    t = threading.Thread(
        target=lambda: app.run(host=host, port=port, use_reloader=False, debug=False),
        daemon=True,
        name="whitelist-gui",
    )
    t.start()


# ---------------------------------------------------------------------------
# Redis helpers (pure functions, no Flask dependency — independently testable)
# ---------------------------------------------------------------------------

def _get_redis_client(cfg: dict) -> redis_lib.Redis:
    return redis_lib.Redis(
        host=cfg["redis_host"],
        port=int(cfg["redis_port"]),
        decode_responses=True,
    )


def _list_whitelist(r: redis_lib.Redis) -> list:
    """Return sorted list of {number, comment} dicts for all whitelist entries."""
    entries = []
    cursor = 0
    while True:
        cursor, keys = r.scan(cursor, match="+*", count=100)
        for key in keys:
            if key.startswith("score:") or key.startswith(COMMENT_KEY_PREFIX):
                continue
            comment = r.get(COMMENT_KEY_PREFIX + key) or ""
            entries.append({"number": key, "comment": comment})
        if cursor == 0:
            break
    entries.sort(key=lambda e: e["number"])
    return entries


def _normalize_number(raw: str, default_country: str) -> "str | None":
    """Parse raw input and return E.164 string, or None on failure."""
    try:
        parsed = phonenumbers.parse(raw.strip(), default_country)
        if not phonenumbers.is_valid_number(parsed):
            return None
        return "+" + str(parsed.country_code) + str(parsed.national_number)
    except phonenumbers.NumberParseException:
        return None


def _add_entry(r: redis_lib.Redis, number: str, comment: str) -> None:
    r.set(number, "1")
    if comment:
        r.set(COMMENT_KEY_PREFIX + number, comment.strip())
    else:
        r.delete(COMMENT_KEY_PREFIX + number)


def _delete_entry(r: redis_lib.Redis, number: str) -> None:
    r.delete(number)
    r.delete(COMMENT_KEY_PREFIX + number)


# ---------------------------------------------------------------------------
# Auth decorator
# ---------------------------------------------------------------------------

def _require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user = _gui_config.get("whitelist_gui_user")
        pw = _gui_config.get("whitelist_gui_password")
        if user and pw:
            auth = request.authorization
            if not auth or auth.username != user or auth.password != pw:
                return Response(
                    "Unauthorized",
                    401,
                    {"WWW-Authenticate": 'Basic realm="Whitelist GUI"'},
                )
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
@_require_auth
def index():
    try:
        r = _get_redis_client(_gui_config)
        entries = _list_whitelist(r)
    except redis_lib.exceptions.RedisError as exc:
        entries = []
        flash(f"Redis error: {exc}", "error")
    return render_template_string(TEMPLATE_INDEX, entries=entries)


@app.route("/add", methods=["POST"])
@_require_auth
def add():
    raw = request.form.get("number", "").strip()
    comment = request.form.get("comment", "").strip()
    default_country = _gui_config.get("default_country", "DE")
    number = _normalize_number(raw, default_country)
    if number is None:
        flash(
            f"Invalid phone number: {raw!r}. Use E.164 (+491636…) or a local number.",
            "error",
        )
        return redirect(url_for("index"))
    try:
        r = _get_redis_client(_gui_config)
        if r.exists(number):
            flash(f"{number} is already whitelisted.", "warning")
        else:
            _add_entry(r, number, comment)
            flash(f"{number} added to whitelist.", "success")
    except redis_lib.exceptions.RedisError as exc:
        flash(f"Redis error: {exc}", "error")
    return redirect(url_for("index"))


@app.route("/edit/<path:number>")
@_require_auth
def edit(number):
    try:
        r = _get_redis_client(_gui_config)
        if not r.exists(number):
            flash(f"{number} not found.", "error")
            return redirect(url_for("index"))
        comment = r.get(COMMENT_KEY_PREFIX + number) or ""
    except redis_lib.exceptions.RedisError as exc:
        flash(f"Redis error: {exc}", "error")
        return redirect(url_for("index"))
    return render_template_string(TEMPLATE_EDIT, number=number, comment=comment)


@app.route("/edit/<path:number>", methods=["POST"])
@_require_auth
def edit_save(number):
    new_raw = request.form.get("number", "").strip()
    new_comment = request.form.get("comment", "").strip()
    default_country = _gui_config.get("default_country", "DE")
    new_number = _normalize_number(new_raw, default_country)
    if new_number is None:
        flash(f"Invalid phone number: {new_raw!r}.", "error")
        return redirect(url_for("edit", number=number))
    try:
        r = _get_redis_client(_gui_config)
        if new_number != number:
            _delete_entry(r, number)
        _add_entry(r, new_number, new_comment)
        flash(f"Updated: {new_number}.", "success")
    except redis_lib.exceptions.RedisError as exc:
        flash(f"Redis error: {exc}", "error")
    return redirect(url_for("index"))


@app.route("/delete/<path:number>", methods=["POST"])
@_require_auth
def delete(number):
    try:
        r = _get_redis_client(_gui_config)
        _delete_entry(r, number)
        flash(f"{number} removed from whitelist.", "success")
    except redis_lib.exceptions.RedisError as exc:
        flash(f"Redis error: {exc}", "error")
    return redirect(url_for("index"))


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

_COMMON_HEAD = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Whitelist Manager</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; }
    body { font-family: sans-serif; max-width: 900px; margin: 2rem auto; padding: 0 1rem; color: #222; }
    h1 { margin-bottom: 1.5rem; }
    table { width: 100%; border-collapse: collapse; margin-bottom: 1.5rem; }
    th, td { text-align: left; padding: .5rem .75rem; border-bottom: 1px solid #ddd; }
    th { background: #f5f5f5; }
    tr:hover td { background: #fafafa; }
    .btn { display: inline-block; padding: .35rem .8rem; border: 1px solid #999;
           border-radius: 3px; background: #fff; cursor: pointer; font-size: .9rem;
           text-decoration: none; color: #222; }
    .btn:hover { background: #f0f0f0; }
    .btn-danger { border-color: #c00; color: #c00; }
    .btn-danger:hover { background: #fff0f0; }
    .btn-primary { background: #0066cc; border-color: #0055aa; color: #fff; }
    .btn-primary:hover { background: #0055aa; }
    .flash { padding: .6rem 1rem; border-radius: 3px; margin-bottom: 1rem; }
    .flash.success { background: #d4edda; border: 1px solid #c3e6cb; color: #155724; }
    .flash.warning { background: #fff3cd; border: 1px solid #ffeeba; color: #856404; }
    .flash.error   { background: #f8d7da; border: 1px solid #f5c6cb; color: #721c24; }
    .form-row { display: flex; gap: .5rem; align-items: flex-end; flex-wrap: wrap; margin-bottom: 1.5rem; }
    .form-row label { display: block; font-size: .85rem; margin-bottom: .2rem; }
    .form-row input[type=text] { padding: .35rem .6rem; border: 1px solid #aaa;
                                  border-radius: 3px; font-size: .95rem; width: 220px; }
    .form-row input[type=text].wide { width: 320px; }
    .empty { color: #777; font-style: italic; }
    .note { font-size: .8rem; color: #888; margin-top: 2rem; border-top: 1px solid #eee; padding-top: .75rem; }
  </style>
</head>
<body>
"""

TEMPLATE_INDEX = _COMMON_HEAD + """
<h1>Whitelist Manager</h1>

{% with messages = get_flashed_messages(with_categories=true) %}
  {% for category, message in messages %}
    <div class="flash {{ category }}">{{ message }}</div>
  {% endfor %}
{% endwith %}

<h2>Add entry</h2>
<form method="post" action="/add">
  <div class="form-row">
    <div>
      <label for="number">Phone number</label>
      <input id="number" name="number" type="text" class="wide"
             placeholder="+491636209692 or 01636209692" required autofocus>
    </div>
    <div>
      <label for="comment">Comment (optional)</label>
      <input id="comment" name="comment" type="text" class="wide" placeholder="e.g. Mom's mobile">
    </div>
    <div>
      <label>&nbsp;</label>
      <button type="submit" class="btn btn-primary">Add</button>
    </div>
  </div>
</form>

<h2>Whitelisted numbers</h2>
{% if entries %}
<table>
  <thead><tr><th>Number</th><th>Comment</th><th>Actions</th></tr></thead>
  <tbody>
  {% for e in entries %}
    <tr>
      <td><code>{{ e.number }}</code></td>
      <td>{{ e.comment }}</td>
      <td>
        <a href="{{ url_for('edit', number=e.number) }}" class="btn">Edit</a>
        <form method="post" action="{{ url_for('delete', number=e.number) }}"
              style="display:inline"
              onsubmit="return confirm('Remove {{ e.number }} from whitelist?')">
          <button type="submit" class="btn btn-danger">Delete</button>
        </form>
      </td>
    </tr>
  {% endfor %}
  </tbody>
</table>
{% else %}
  <p class="empty">No entries in whitelist yet.</p>
{% endif %}

<p class="note">
  Only whitelist entries are shown. Score cache keys (<code>score:*</code>) are managed automatically.
</p>
</body></html>
"""

TEMPLATE_EDIT = _COMMON_HEAD + """
<h1>Edit entry</h1>

{% with messages = get_flashed_messages(with_categories=true) %}
  {% for category, message in messages %}
    <div class="flash {{ category }}">{{ message }}</div>
  {% endfor %}
{% endwith %}

<form method="post">
  <div class="form-row">
    <div>
      <label for="number">Phone number</label>
      <input id="number" name="number" type="text" class="wide"
             value="{{ number }}" required autofocus>
    </div>
    <div>
      <label for="comment">Comment</label>
      <input id="comment" name="comment" type="text" class="wide" value="{{ comment }}">
    </div>
    <div>
      <label>&nbsp;</label>
      <button type="submit" class="btn btn-primary">Save</button>
      <a href="{{ url_for('index') }}" class="btn">Cancel</a>
    </div>
  </div>
</form>
</body></html>
"""


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import os
    import sys

    cfg = {
        "redis_host": os.environ.get("REDIS_HOST", "127.0.0.1"),
        "redis_port": int(os.environ.get("REDIS_PORT", "6379")),
        "default_country": os.environ.get("DEFAULT_COUNTRY", "DE"),
        "whitelist_gui_host": os.environ.get("WHITELIST_GUI_HOST", "127.0.0.1"),
        "whitelist_gui_port": int(os.environ.get("WHITELIST_GUI_PORT", "8080")),
        "whitelist_gui_user": os.environ.get("WHITELIST_GUI_USER"),
        "whitelist_gui_password": os.environ.get("WHITELIST_GUI_PASSWORD"),
    }
    if not cfg["redis_host"]:
        print("REDIS_HOST must be set", file=sys.stderr)
        sys.exit(1)

    logging.basicConfig(level=logging.DEBUG)
    _gui_config.update(cfg)
    app.secret_key = secrets.token_hex(32)
    print(f"Starting whitelist GUI on http://{cfg['whitelist_gui_host']}:{cfg['whitelist_gui_port']}/")
    app.run(
        host=cfg["whitelist_gui_host"],
        port=cfg["whitelist_gui_port"],
        debug=True,
    )
