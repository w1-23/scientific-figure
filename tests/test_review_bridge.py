"""Local review: served SVG, version-bound feedback, no silent stale writes."""
import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from scientific_figure.cli import execute, parser
from scientific_figure.project import write_project
from scientific_figure.review_bridge import (ReviewServer, StaleFeedback, create_session,
                                             current_figure, feedback_status, read_log,
                                             record_feedback, sanitize_svg,
                                             svg_object_ids, wait_for_feedback)


def scene(fill="#cfe8c0", text="Cell"):
    return {"schema_version": "0.3", "revision": 0,
            "canvas": {"width": 200, "height": 120, "background": "#ffffff"},
            "objects": [
                {"id": "cell", "kind": "ellipse",
                 "geometry": {"cx": 100, "cy": 60, "rx": 60, "ry": 40},
                 "style": {"fill": fill, "stroke": "#335522", "stroke_width": 2}},
                {"id": "label", "kind": "text",
                 "geometry": {"x": 20, "y": 110, "text": text},
                 "style": {"font_size": 12}}]}


def preview(tmp_path, name="preview", **kwargs):
    path = tmp_path / name
    write_project(scene(**kwargs), path, scale=1, preview=True)
    return path


def payload(figure, session, *, status="changes_requested", comments=None, sha=None):
    return {"schema_version": "0.1", "figure_id": session["figure_id"],
            "svg_sha256": sha if sha is not None else figure["svg_sha256"],
            "scene_revision": figure["revision"], "status": status,
            "comments": comments if comments is not None else [
                {"object_id": "cell", "position": [100, 60], "comment": "Move the membrane."}]}


def server_request(url, *, method="GET", data=None):
    body = None if data is None else json.dumps(data).encode("utf-8")
    request = urllib.request.Request(url, data=body, method=method)
    if body is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read().decode("utf-8"), response.headers
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8"), error.headers


def test_current_figure_binds_and_sanitizes_the_published_svg(tmp_path):
    figure = current_figure(preview(tmp_path))
    assert figure["kind"] == "draft_preview" and figure["revision"] == 0
    assert figure["svg"].startswith("<?xml")
    assert {"cell", "label"} <= svg_object_ids(figure["svg"])
    assert len(figure["svg_sha256"]) == 64


@pytest.mark.parametrize("markup", [
    '<script>alert(1)</script>',
    '<rect onload="alert(1)" width="1" height="1"/>',
    '<image href="https://example.com/a.png"/>',
    '<use href="other.svg#a"/>',
])
def test_sanitizer_rejects_scripts_handlers_and_external_references(markup):
    svg = '<svg xmlns="http://www.w3.org/2000/svg">' + markup + "</svg>"
    with pytest.raises(ValueError):
        sanitize_svg(svg)


def test_sanitizer_rejects_doctype_and_non_svg(tmp_path):
    with pytest.raises(ValueError, match="document type"):
        sanitize_svg('<!DOCTYPE svg><svg xmlns="http://www.w3.org/2000/svg"/>')
    with pytest.raises(ValueError, match="not an SVG"):
        sanitize_svg("<html/>")


def test_session_reuse_and_source_isolation(tmp_path):
    first = preview(tmp_path, "one")
    session = create_session(first)
    assert session["session_reused"] is False
    again = create_session(first)
    assert again["session_reused"] is True and again["session"] == session["session"]
    second = preview(tmp_path, "two")
    with pytest.raises(ValueError, match="another source"):
        create_session(second, session["session"])


def test_feedback_is_bound_to_the_shown_version(tmp_path):
    source = preview(tmp_path)
    session = create_session(source)
    figure = current_figure(source)
    entry = record_feedback(session, figure, payload(figure, session))
    assert entry["round"] == 1 and entry["svg_sha256"] == figure["svg_sha256"]
    assert read_log(session["session"])["rounds"][0]["comments"][0]["object_id"] == "cell"
    preview(tmp_path, fill="#ffddaa")
    changed = current_figure(source)
    assert changed["svg_sha256"] != figure["svg_sha256"]
    with pytest.raises(StaleFeedback, match="stale feedback"):
        record_feedback(session, changed, payload(figure, session))
    assert len(read_log(session["session"])["rounds"]) == 1
    recorded = record_feedback(session, changed, payload(changed, session))
    assert recorded["round"] == 2


def test_feedback_validation_rejects_unknown_objects_and_bad_fields(tmp_path):
    source = preview(tmp_path)
    session = create_session(source)
    figure = current_figure(source)
    with pytest.raises(ValueError, match="unknown object"):
        record_feedback(session, figure, payload(figure, session, comments=[
            {"object_id": "missing", "position": [1, 2], "comment": "x"}]))
    extra = payload(figure, session)
    extra["extra"] = True
    with pytest.raises(ValueError, match="Additional properties"):
        record_feedback(session, figure, extra)
    long_comment = payload(figure, session, comments=[
        {"object_id": None, "position": [1, 2], "comment": "x" * 2001}])
    with pytest.raises(ValueError):
        record_feedback(session, figure, long_comment)
    wrong_figure = payload(figure, session)
    wrong_figure["figure_id"] = "other"
    with pytest.raises(ValueError, match="figure_id"):
        record_feedback(session, figure, wrong_figure)


def test_server_serves_svg_state_and_records_feedback(tmp_path):
    source = preview(tmp_path)
    session = create_session(source)
    server = ReviewServer(source, session, port=0)
    info = server.start()
    try:
        base = f"http://127.0.0.1:{info['port']}"
        query = "?t=" + info["token"]
        status, html, headers = server_request(info["url"])
        assert status == 200 and "Scientific Figure" in html and "批注" in html
        assert "default-src 'none'" in headers.get("Content-Security-Policy", "")
        assert server_request(base + "/svg")[0] == 403
        status, served, headers = server_request(base + "/svg" + query)
        assert status == 200 and served.lstrip().startswith("<?xml") and "image/svg" in headers.get("Content-Type")
        status, state_text, _ = server_request(base + "/state" + query)
        state = json.loads(state_text)
        assert status == 200 and state["figure_id"] == session["figure_id"]
        assert state["feedback"] is None and state["rounds"] == 0
        figure = current_figure(source)
        status, body, _ = server_request(base + "/feedback" + query, method="POST",
                                         data=payload(figure, session))
        assert status == 200 and json.loads(body)["round"]["round"] == 1
        state = json.loads(server_request(base + "/state" + query)[1])
        assert state["feedback"]["status"] == "changes_requested" and state["rounds"] == 1
    finally:
        server.stop()


def test_server_refuses_stale_and_unknown_feedback(tmp_path):
    source = preview(tmp_path)
    session = create_session(source)
    server = ReviewServer(source, session, port=0)
    info = server.start()
    try:
        base = f"http://127.0.0.1:{info['port']}" + "/feedback?t=" + info["token"]
        old = current_figure(source)
        preview(tmp_path, fill="#ffddaa")
        status, body, _ = server_request(base, method="POST", data=payload(old, session))
        assert status == 409 and "stale" in json.loads(body)["error"]
        current = current_figure(source)
        bad = payload(current, session, comments=[
            {"object_id": "missing", "position": [1, 2], "comment": "x"}])
        assert server_request(base, method="POST", data=bad)[0] == 400
    finally:
        server.stop()


def test_status_reports_a_stale_submission_after_a_new_version(tmp_path):
    source = preview(tmp_path)
    session = create_session(source)
    figure = current_figure(source)
    record_feedback(session, figure, payload(figure, session))
    preview(tmp_path, fill="#ffddaa")
    result = feedback_status(session["session"])
    assert result["status"] == "feedback" and result["stale"] is True
    assert result["latest"]["svg_sha256"] == figure["svg_sha256"]
    assert result["current"]["svg_sha256"] != figure["svg_sha256"]


def test_wait_for_feedback_times_out_and_then_returns_a_round(tmp_path):
    source = preview(tmp_path)
    session = create_session(source)
    with pytest.raises(TimeoutError):
        wait_for_feedback(session["session"], timeout=0.1, interval=0.02)

    def submit():
        time.sleep(0.15)
        figure = current_figure(source)
        record_feedback(session, figure, payload(figure, session, status="approved", comments=[]))

    thread = threading.Thread(target=submit)
    thread.start()
    entry = wait_for_feedback(session["session"], timeout=5, interval=0.02)
    thread.join()
    assert entry["status"] == "approved" and entry["round"] == 1


def test_cli_review_status_reports_the_latest_round(tmp_path):
    source = preview(tmp_path)
    session = create_session(source)
    figure = current_figure(source)
    record_feedback(session, figure, payload(figure, session, status="approved", comments=[]))
    result = execute(parser().parse_args(["review-status", session["session"]]))
    assert result["status"] == "feedback" and result["latest"]["status"] == "approved"
    assert result["figure_id"] == session["figure_id"] and result["rounds"] == 1
