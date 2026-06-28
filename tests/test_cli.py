import shutil
import subprocess
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import patch

import pytest

from openprint.cli import main as cli_main


def test_cli_help():
    result = subprocess.run(
        [sys.executable, "-m", "openprint.cli", "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "print" in result.stdout.lower()
    assert "discover" in result.stdout.lower()
    assert "bridge" in result.stdout.lower()


def test_cli_no_args():
    result = subprocess.run(
        [sys.executable, "-m", "openprint.cli"],
        capture_output=True, text=True,
    )
    output = result.stdout + result.stderr
    assert "print" in output.lower()


def test_looks_like_target():
    assert cli_main._looks_like_target("http://host:631")
    assert cli_main._looks_like_target("ipp://host/ipp/print")
    assert cli_main._looks_like_target("192.168.1.5")
    assert cli_main._looks_like_target("printer.local")
    assert cli_main._looks_like_target("host:631")
    # A human-readable printer name is not a target.
    assert not cli_main._looks_like_target("HP LaserJet 4000")


def test_resolve_printer_arg_passthrough():
    # URLs / hosts pass straight through without discovery.
    assert cli_main._resolve_printer_arg("ipp://1.2.3.4/ipp/print") == "ipp://1.2.3.4/ipp/print"
    assert cli_main._resolve_printer_arg(None) is None


def test_cmd_print_routes_through_integrations():
    # Any file type (here .txt) is accepted and handed to the integrations layer.
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
        f.write(b"hello")
        txt_path = f.name

    captured = {}

    def fake_print_file(path, **kwargs):
        captured["path"] = str(path)
        captured["kwargs"] = kwargs
        return {"id": "job_1", "status": "accepted", "printer": "ipp://x/ipp/print"}

    args = types.SimpleNamespace(
        file=txt_path, printer="ipp://x/ipp/print", copies=2,
        bw=True, duplex="none", media="letter", pages="1-2",
    )
    with patch("openprint.integrations.print_file", fake_print_file):
        cli_main.cmd_print(args)

    assert captured["path"] == txt_path
    assert captured["kwargs"]["printer_url"] == "ipp://x/ipp/print"
    assert captured["kwargs"]["copies"] == 2
    assert captured["kwargs"]["color"] is False
    assert captured["kwargs"]["pages"] == "1-2"


def test_cmd_print_missing_file_exits():
    args = types.SimpleNamespace(
        file=str(Path(tempfile.gettempdir()) / "nope-zzz.pdf"), printer=None,
        copies=1, bw=False, duplex="none", media="a4", pages="all",
    )
    with pytest.raises(SystemExit):
        cli_main.cmd_print(args)


def test_opp_entrypoint():
    # Resolve the console script via shutil.which so the .exe is found on
    # Windows (bare "opp" isn't located by CreateProcess without PATHEXT).
    opp = shutil.which("opp")
    if opp is None:
        pytest.skip("opp console script not installed on PATH")
    result = subprocess.run(
        [opp, "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "print" in result.stdout.lower()
