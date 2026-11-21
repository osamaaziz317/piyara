"""Private, session-lifetime HTML files for browser page/print previews."""

from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4


class PreviewFiles:
    """Keep preview pages readable until sign-out or application shutdown."""

    def __init__(self):
        self._directory = None

    def destination(self):
        if self._directory is None:
            # A platform/browser lock must not prevent sign-out or shutdown.
            self._directory = TemporaryDirectory(prefix="bifma-preview-", ignore_cleanup_errors=True)
        return Path(self._directory.name) / (uuid4().hex + ".html")

    def close(self):
        if self._directory is not None:
            self._directory.cleanup()
            self._directory = None
