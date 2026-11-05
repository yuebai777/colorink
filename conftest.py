# Shared pytest configuration for the Colorink suite.
#
# Windows-only teardown fix: pytest's cleanup_dead_symlinks calls
# Path.unlink() on the "pytest-current" directory symlink, which raises
# PermissionError on Windows (directory symlinks must be removed with
# rmdir) and turns an otherwise-green run into exit code 1. Patch the
# helper (in both _pytest.pathlib and _pytest.tmpdir, which import it as
# a module global) to fall back to rmdir.

import os
import pytest
import _pytest.pathlib as _pathlib
import _pytest.tmpdir as _tmpdir


def _cleanup_dead_symlinks(root):
    for left_dir in root.iterdir():
        if left_dir.is_symlink() and not left_dir.resolve().exists():
            try:
                left_dir.unlink()
            except OSError:
                try:
                    left_dir.rmdir()
                except OSError:
                    pass


_pathlib.cleanup_dead_symlinks = _cleanup_dead_symlinks
_tmpdir.cleanup_dead_symlinks = _cleanup_dead_symlinks


@pytest.fixture(scope="session", autouse=True)
def _isolate_session_appdata(tmp_path_factory):
    """Redirect APPDATA for the test session so no test reads or overwrites the
    user's real %APPDATA%\\Colorink configuration or companion session files."""
    old_appdata = os.environ.get("APPDATA")
    session_appdata = tmp_path_factory.mktemp("colorink_appdata")
    os.environ["APPDATA"] = str(session_appdata)
    try:
        yield session_appdata
    finally:
        if old_appdata is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = old_appdata

