import json
import re
import shutil
import subprocess

import pytest

from app import config

JS = (config.STATIC_DIR / "app.js").read_text(encoding="utf-8")
HTML = (config.STATIC_DIR / "index.html").read_text(encoding="utf-8")
CSS = (config.STATIC_DIR / "styles.css").read_text(encoding="utf-8")
NODE = shutil.which("node")


def test_app_js_never_builds_html_from_strings_or_evals_code():
    for sink in (r"innerHTML", r"outerHTML", r"insertAdjacentHTML", r"document\.write", r"\beval\(", r"new Function", r"setTimeout\(\s*[\"']", r"\.srcdoc", r"javascript:"):
        assert not re.search(sink, JS), sink


def test_credentials_are_never_kept_in_browser_storage_or_readable_cookies():
    assert not re.search(r"localStorage|sessionStorage|document\.cookie|indexedDB", JS)
    assert 'credentials: "same-origin"' in JS and JS.count("X-CSRF-Token") >= 2


def test_index_html_has_no_inline_script_style_or_event_handlers_and_no_remote_resources():
    scripts = re.findall(r"<script\b([^>]*)>(.*?)</script>", HTML, flags=re.S)
    assert scripts and all(("src=" in a and not body.strip()) for a, body in scripts)
    assert not re.search(r"<style\b", HTML) and not re.search(r"\sstyle\s*=", HTML) and not re.search(r"\son\w+\s*=", HTML)
    assert not re.findall(r"""(?:src|href)\s*=\s*["']https?://""", HTML) and "fonts.googleapis" not in HTML + CSS
    assert "@import" not in CSS and not re.search(r"url\(\s*[\"']?https?:", CSS)


def test_login_form_has_show_hide_password_labels_and_no_demo_credentials_in_static_files():
    assert 'id="pw-toggle"' in HTML and 'type="password"' in HTML and 'autocomplete="current-password"' in HTML and "<label" in HTML
    from app.services.seed_service import DEMO_PASSWORDS
    assert not any(p in HTML + JS + CSS for p in DEMO_PASSWORDS.values())              # demo passwords come only from the dev-only API


def test_ui_has_the_required_elements():
    for needle in ("Your campus, connected.", "CampusMate AI", "chat-form", "conv-list", "btn-new", "btn-retry", "provider-pill", "role=\"log\"", "modal", "tpl-staff", "tabs"):
        assert needle in HTML, needle
    for q in ("What is my current fee status?", "Am I eligible for a scholarship?", "What is the policy for changing courses?", "I could not complete my fee payment. What should I do?",
              "Create a support request for me.", "What documents are required for a scholarship application?"):
        assert q in JS
    for feature in ("approvalCard", "showEntry", "traceDetails", "approvalsPanel", "ticketsPanel", "auditPanel", "tracesPanel", "evaluationPanel", "usersPanel", "event-stream"):
        assert feature in JS, feature


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_app_js_parses():
    assert subprocess.run([NODE, "--check", str(config.STATIC_DIR / "app.js")], capture_output=True).returncode == 0


PAYLOADS = ["<script>alert(1)</script>", "<img src=x onerror=alert(1)>", "**<b onmouseover=alert(1)>x</b>**", "| h |\n|---|\n| <iframe src=javascript:alert(1)> |", "### <svg onload=alert(1)>",
            "- <a href=\"javascript:alert(1)\">x</a>", "[F.3.1\"><img src=x onerror=alert(1)>]", "[click](javascript:alert(1))", "`<img src=x onerror=1>`", "*<script>*", "[F.3.1, F.3.2] and [X.9]"]
ALLOWED = {"P", "STRONG", "EM", "CODE", "H3", "H4", "H5", "UL", "LI", "TABLE", "THEAD", "TBODY", "TR", "TH", "TD", "DIV", "BUTTON", "BR"}


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_markdown_renderer_turns_hostile_input_into_inert_text():
    out = subprocess.run([NODE, str(config.ROOT / "tests" / "js" / "render_test.js"), str(config.STATIC_DIR / "app.js")], input=json.dumps(PAYLOADS), capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    for r in json.loads(out.stdout):
        tags = {e["tag"] for e in r["elements"]}
        assert tags <= ALLOWED, (r["payload"], tags - ALLOWED)                          # never a script/img/iframe/a/svg element
        for e in r["elements"]:
            assert set(e["attrs"]) <= {"class", "type", "data-ref"}, (r["payload"], e)  # never an on* handler or href/src
            if e["tag"] == "BUTTON":
                assert re.fullmatch(r"[A-Z](\.\d+)+", e["ref"] or ""), e
    by = {r["payload"]: r for r in json.loads(out.stdout)}
    assert "<script>alert(1)</script>" in by[PAYLOADS[0]]["text"]                       # the markup survives only as visible text
    chips = [e["ref"] for e in by[PAYLOADS[-1]]["elements"] if e["tag"] == "BUTTON"]
    assert chips == ["F.3.1", "F.3.2", "X.9"]
    assert not [e for e in by[PAYLOADS[6]]["elements"] if e["tag"] == "BUTTON"]         # a forged chip with markup in it is not made clickable


def test_csp_blocks_what_the_ui_does_not_need(app):
    from fastapi.testclient import TestClient
    csp = TestClient(app).get("/").headers["content-security-policy"]
    assert "script-src 'self'" in csp and "unsafe-inline" not in csp and "unsafe-eval" not in csp and "object-src 'none'" in csp
    assert TestClient(app).get("/static/app.js").status_code == 200 and TestClient(app).get("/").status_code == 200
