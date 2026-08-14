# Shared pytest configuration for the Colorink suite.
#
# Windows-only teardown fix: pytest's cleanup_dead_symlinks calls
# Path.unlink() on the "pytest-current" directory symlink, which raises
# PermissionError on Windows (directory symlinks must be removed with
# rmdir) and turns an otherwise-green run into exit code 1. Patch the
# helper (in both _pytest.pathlib and _pytest.tmpdir, which import it as
# a module global) to fall back to rmdir.

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
