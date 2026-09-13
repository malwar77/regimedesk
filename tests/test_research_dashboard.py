"""Tests: research dashboard (ported module).

Streamlit is an optional dependency — when it's not installed the
tests skip instead of fail. What we always verify: the file parses as
valid Python and the state-file paths it reads are the repo-root ones
(briefs/, state/) produced by the research swarm."""
import ast
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

APP = Path(__file__).resolve().parents[1] / "research" / "dashboard" / "app.py"


def test_dashboard_file_parses():
    ast.parse(APP.read_text())


def test_dashboard_reads_repo_root_state():
    src = APP.read_text()
    # after the port, app.py sits two levels under repo root
    assert "parents[2]" in src
    assert '"state"' in src or "/ 'state'" in src
    assert '"briefs"' in src or "/ 'briefs'" in src


def test_dashboard_imports_when_streamlit_present():
    pytest = __import__("pytest")
    pytest.importorskip("streamlit")
    import importlib.util
    spec = importlib.util.spec_from_file_location("research_dashboard_app", APP)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
