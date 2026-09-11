"""Release validation must not depend on the host's default text encoding."""
from pathlib import Path
import runpy
from unittest import mock


def test_release_validation_with_windows_default_encoding():
    original = Path.read_text

    def windows_read_text(path, encoding=None, errors=None):
        return original(path, encoding=encoding or 'cp1252', errors=errors)

    with mock.patch.object(Path, 'read_text', windows_read_text):
        runpy.run_path(str(Path(__file__).resolve().parents[1] / 'scripts/check-release.py'))
