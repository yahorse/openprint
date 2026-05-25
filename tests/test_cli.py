import subprocess
import sys


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


def test_opp_entrypoint():
    result = subprocess.run(
        ["opp", "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "print" in result.stdout.lower()
