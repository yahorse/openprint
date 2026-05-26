
from openprint.dashboard import STATIC_DIR


def test_static_dir_exists():
    assert STATIC_DIR.exists()


def test_index_html_exists():
    assert (STATIC_DIR / "index.html").exists()


def test_index_html_has_content():
    content = (STATIC_DIR / "index.html").read_text()
    assert "OpenPrint" in content
    assert "/opp/v1" in content
