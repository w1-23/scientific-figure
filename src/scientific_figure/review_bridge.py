"""A local review bridge: serve the current SVG and record version-bound feedback.

The bridge never edits a Scene or a project. It reads the published ``figure.svg``
from a preview/project directory, sanitizes it for inline display, and stores
host or user feedback next to the review session, bound to the exact SVG bytes
that were shown. Feedback from an older version is rejected, never silently
applied to a newer one.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import xml.etree.ElementTree as ET

from .prepared import _fields
from .review_page import PAGE
from .scene import load_json, schema_validate

SCHEMA_VERSION = "0.1"
MAX_SVG_BYTES = 8 * 1024 * 1024
MAX_BODY_BYTES = 256 * 1024
MAX_ELEMENTS = 50_000
FORBIDDEN_TAGS = {"script", "foreignObject", "iframe", "image", "video", "audio", "a", "use"}
_HANDLER = re.compile(r"^on", re.IGNORECASE)
_URL = re.compile(r"url\(\s*([^)]+?)\s*\)", re.IGNORECASE)


class StaleFeedback(ValueError):
    """The submitted SVG digest is not the version currently published."""


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _atomic_write(path, data):
    path = Path(path)
    temporary = path.with_name("." + path.name + "." + secrets.token_hex(6))
    temporary.write_bytes(data)
    os.replace(temporary, path)


def _read_bounded(path, limit):
    with Path(path).open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("figure SVG exceeds the review byte budget")
    return data


def sanitize_svg(text):
    """Reject scripts, external references, handlers and non-SVG roots."""
    if "<!DOCTYPE" in text or "<?xml-stylesheet" in text:
        raise ValueError("SVG document type or stylesheet is not allowed")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as error:
        raise ValueError("invalid SVG: " + str(error)) from error
    if _local(root.tag) != "svg":
        raise ValueError("review source is not an SVG document")
    ET.register_namespace("", "http://www.w3.org/2000/svg")
    ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")
    count = 0
    for element in root.iter():
        count += 1
        if count > MAX_ELEMENTS:
            raise ValueError("SVG element budget exceeded")
        if _local(element.tag) in FORBIDDEN_TAGS:
            raise ValueError("SVG contains an unsupported element: " + _local(element.tag))
        for name, value in element.attrib.items():
            local = _local(name)
            if _HANDLER.match(local):
                raise ValueError("SVG contains an event handler attribute")
            if local == "href" and value.strip() and not value.strip().startswith("#"):
                raise ValueError("SVG contains an external reference")
            if local == "style":
                for reference in _URL.findall(value):
                    if not reference.strip("'\" ").startswith("#"):
                        raise ValueError("SVG style references an external resource")
    return ET.tostring(root, encoding="unicode", xml_declaration=True) + "\n"


def svg_object_ids(text):
    root = ET.fromstring(text)
    return {element.get("id") for element in root.iter()
            if element.get("id") and not element.get("id").startswith("sf-")}


def _figure_meta(directory):
    preview = directory / "preview.json"
    if preview.is_file():
        return load_json(preview).get("revision"), "draft_preview"
    receipt = directory / "receipt.json"
    if receipt.is_file():
        return load_json(receipt).get("revision"), "project"
    return None, "svg"


def current_figure(source):
    """Read, sanitize and hash the figure currently published by ``source``."""
    path = Path(source)
    if path.is_dir():
        svg_path = path / "figure.svg"
        revision, kind = _figure_meta(path)
    elif path.suffix.lower() == ".svg":
        svg_path, revision, kind = path, None, "svg"
    else:
        raise ValueError("review source must be an SVG file, a preview or a project directory")
    try:
        data = _read_bounded(svg_path, MAX_SVG_BYTES)
    except FileNotFoundError:
        time.sleep(0.05)  # an atomic publish may be between rename steps
        data = _read_bounded(svg_path, MAX_SVG_BYTES)
    try:
        text = data.decode("utf-8")
    except UnicodeError as error:
        raise ValueError("figure SVG is not UTF-8") from error
    clean = sanitize_svg(text)
    encoded = clean.encode("utf-8")
    if len(encoded) > MAX_SVG_BYTES:
        raise ValueError("sanitized figure SVG exceeds the review byte budget")
    if type(revision) not in {int, type(None)} or (revision is not None and revision < 0):
        revision = None
    figure_id = path.name if path.is_dir() else path.stem
    return {"source": str(path.resolve()), "svg": clean,
            "svg_sha256": hashlib.sha256(encoded).hexdigest(),
            "revision": revision, "kind": kind, "figure_id": figure_id}


def _default_session(source):
    path = Path(source).resolve()
    return path.parent / (path.name + "-review")


def create_session(source, session_dir=None):
    """Create or reuse a review session directory; never mix two sources."""
    source = Path(source).resolve()
    figure = current_figure(source)
    directory = (Path(session_dir) if session_dir else _default_session(source)).resolve()
    marker = directory / "session.json"
    if marker.is_file():
        existing = load_json(marker)
        if existing.get("source") != str(source):
            raise ValueError("review session belongs to another source")
        return {**existing, "session": str(directory), "session_reused": True}
    if directory.exists() and any(directory.iterdir()):
        raise ValueError("review session directory is not empty and has no session.json")
    directory.mkdir(parents=True, exist_ok=True)
    session = {"schema_version": SCHEMA_VERSION, "kind": "review_session",
               "source": str(source), "figure_id": figure["figure_id"], "created_at": _now()}
    _atomic_write(marker, (json.dumps(session, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return {**session, "session": str(directory), "session_reused": False}


def _log_path(session_dir):
    return Path(session_dir) / "feedback.json"


def read_log(session_dir):
    path = _log_path(session_dir)
    if not path.is_file():
        return {"schema_version": SCHEMA_VERSION, "kind": "review_feedback_log", "rounds": []}
    document = load_json(path)
    _fields(document, ("schema_version", "kind", "rounds"))
    if document["schema_version"] != SCHEMA_VERSION or document["kind"] != "review_feedback_log":
        raise ValueError("unsupported feedback log")
    if not isinstance(document["rounds"], list):
        raise ValueError("feedback rounds must be a list")
    return document


def validate_feedback(value, *, figure_id=None, object_ids=None):
    value = json.loads(json.dumps(value, ensure_ascii=False))
    schema_validate(value, "review-feedback")
    if figure_id is not None and value["figure_id"] != figure_id:
        raise ValueError("feedback figure_id does not match this session")
    if object_ids is not None:
        unknown = sorted({comment["object_id"] for comment in value["comments"]
                          if comment["object_id"] is not None} - set(object_ids))
        if unknown:
            raise ValueError("feedback names unknown object IDs: " + ", ".join(unknown))
    return value


def record_feedback(session, figure, value):
    document = read_log(session["session"])
    value = validate_feedback(value, figure_id=session["figure_id"],
                              object_ids=svg_object_ids(figure["svg"]))
    if value["svg_sha256"] != figure["svg_sha256"]:
        raise StaleFeedback("stale feedback: the figure changed since this version")
    comments = []
    for comment in value["comments"]:
        comments.append({"object_id": comment["object_id"], "position": comment["position"],
                         "comment": comment["comment"],
                         "svg_sha256": comment.get("svg_sha256") or value["svg_sha256"]})
    entry = {"schema_version": SCHEMA_VERSION, "kind": "review_feedback",
             "round": len(document["rounds"]) + 1, "figure_id": value["figure_id"],
             "svg_sha256": value["svg_sha256"], "scene_revision": value["scene_revision"],
             "status": value["status"], "comments": comments, "received_at": _now()}
    document["rounds"].append(entry)
    _atomic_write(_log_path(session["session"]),
                  (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return entry


def feedback_status(session_dir):
    directory = Path(session_dir).resolve()
    session = load_json(directory / "session.json")
    _fields(session, ("schema_version", "kind", "source", "figure_id", "created_at"))
    figure = current_figure(session["source"])
    document = read_log(directory)
    latest = document["rounds"][-1] if document["rounds"] else None
    return {"status": "feedback" if latest else "no_feedback",
            "session": str(directory), "figure_id": session["figure_id"],
            "current": {"svg_sha256": figure["svg_sha256"], "revision": figure["revision"],
                        "kind": figure["kind"]},
            "rounds": len(document["rounds"]), "latest": latest,
            "stale": bool(latest and latest["svg_sha256"] != figure["svg_sha256"])}


def wait_for_feedback(session_dir, *, timeout=600.0, interval=0.5):
    start = len(read_log(session_dir)["rounds"])
    deadline = time.monotonic() + float(timeout)
    while time.monotonic() < deadline:
        rounds = read_log(session_dir)["rounds"]
        if len(rounds) > start:
            return rounds[-1]
        time.sleep(interval)
    raise TimeoutError("no review feedback before the timeout")


class ReviewServer:
    """Threaded localhost server; the caller owns start/stop and the URL."""

    def __init__(self, source, session, *, host="127.0.0.1", port=0):
        self.source = str(Path(source).resolve())
        self.session = session
        self.session_dir = session["session"]
        self.host, self.port = host, int(port)
        self.token = secrets.token_urlsafe(24)
        self.httpd = None
        self.thread = None

    def info(self):
        return {"status": "serving", "url": f"http://{self.host}:{self.port}/?t={self.token}",
                "host": self.host, "port": self.port, "token": self.token,
                "session": self.session_dir, "source": self.source}

    def start(self):
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_GET(self):
                bridge._get(self)

            def do_POST(self):
                bridge._post(self)

        self.httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        return self.info()

    def stop(self):
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
        if self.thread is not None:
            self.thread.join(timeout=5)
            self.thread = None

    def _authorized(self, query):
        return secrets.compare_digest(query.get("t", [""])[0], self.token)

    @staticmethod
    def _send(handler, status, content_type, body):
        handler.send_response(status)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("X-Content-Type-Options", "nosniff")
        handler.send_header("Referrer-Policy", "no-referrer")
        handler.send_header("Content-Security-Policy",
                            "default-src 'none'; style-src 'unsafe-inline'; "
                            "script-src 'unsafe-inline'; img-src data:; connect-src 'self'; "
                            "base-uri 'none'; form-action 'none'")
        handler.end_headers()
        handler.wfile.write(body)

    def _json(self, handler, status, value):
        self._send(handler, status, "application/json; charset=utf-8",
                   (json.dumps(value, ensure_ascii=False) + "\n").encode("utf-8"))

    def _state(self):
        figure = current_figure(self.source)
        document = read_log(self.session_dir)
        latest = document["rounds"][-1] if document["rounds"] else None
        return {**figure, "feedback": latest, "rounds": len(document["rounds"])}

    def _get(self, handler):
        parsed = urlparse(handler.path)
        if not self._authorized(parse_qs(parsed.query)):
            self._json(handler, 403, {"error": "invalid review token"})
            return
        try:
            if parsed.path in {"/", "/index.html"}:
                self._send(handler, 200, "text/html; charset=utf-8", PAGE.encode("utf-8"))
            elif parsed.path == "/svg":
                figure = current_figure(self.source)
                self._send(handler, 200, "image/svg+xml; charset=utf-8", figure["svg"].encode("utf-8"))
            elif parsed.path == "/state":
                state = self._state()
                state.pop("svg", None)
                state["figure_id"] = self.session["figure_id"]
                self._json(handler, 200, state)
            else:
                self._json(handler, 404, {"error": "unknown review route"})
        except (ValueError, OSError) as error:
            self._json(handler, 500, {"error": f"{type(error).__name__}: {error}"})

    def _post(self, handler):
        parsed = urlparse(handler.path)
        if not self._authorized(parse_qs(parsed.query)):
            self._json(handler, 403, {"error": "invalid review token"})
            return
        if parsed.path != "/feedback":
            self._json(handler, 404, {"error": "unknown review route"})
            return
        try:
            length = int(handler.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if not 1 <= length <= MAX_BODY_BYTES:
            self._json(handler, 413, {"error": "feedback body is missing or too large"})
            return
        try:
            value = json.loads(handler.rfile.read(length).decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            self._json(handler, 400, {"error": "feedback is not valid JSON"})
            return
        try:
            figure = current_figure(self.source)
            entry = record_feedback(self.session, figure, value)
            self._json(handler, 200, {"status": "recorded", "round": entry})
        except StaleFeedback as error:
            current = None
            try:
                figure = current_figure(self.source)
                current = {"svg_sha256": figure["svg_sha256"], "revision": figure["revision"]}
            except (ValueError, OSError):
                pass
            self._json(handler, 409, {"error": str(error), "current": current})
        except (ValueError, OSError) as error:
            self._json(handler, 400, {"error": f"{type(error).__name__}: {error}"})
