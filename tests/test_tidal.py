import asyncio
import tempfile
import unittest
import sys
import types
from pathlib import Path
from unittest.mock import patch

try:
    import aiohttp  # noqa: F401
except ModuleNotFoundError:
    aiohttp_stub = types.ModuleType("aiohttp")
    aiohttp_stub.ContentTypeError = ValueError
    aiohttp_stub.ClientSession = object
    aiohttp_stub.ClientTimeout = object
    aiohttp_stub.BasicAuth = object
    sys.modules["aiohttp"] = aiohttp_stub

from tidal.tidal import TidalExtension


class FakeDB:
    def __init__(self):
        self.updates = []

    def set_config(self, update):
        self.updates.append(update)


class TidalExtensionTests(unittest.TestCase):
    def make_extension(self):
        return TidalExtension(
            "tidal",
            core=object(),
            db=FakeDB(),
            config={"tidal": {"country_code": "US"}},
        )

    def test_status_never_returns_secret(self):
        extension = self.make_extension()
        extension._credentials = {"client_id": "client-1234", "client_secret": "secret"}
        status = extension.on_status()
        self.assertTrue(status["configured"])
        self.assertEqual(status["client_id_hint"], "...1234")
        self.assertNotIn("client_secret", status)

    def test_configure_validates_before_persisting(self):
        extension = self.make_extension()
        with self.assertRaisesRegex(ValueError, "two-letter"):
            asyncio.run(extension.on_configure("client", "secret", "USA"))

    def test_disconnect_removes_credentials_file(self):
        extension = self.make_extension()
        extension._credentials = {"client_id": "client", "client_secret": "secret"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "credentials.json"
            path.write_text("{}", encoding="utf-8")
            with patch("tidal.tidal.CREDENTIALS_PATH", path):
                status = asyncio.run(extension.on_disconnect())
            self.assertFalse(path.exists())
            self.assertFalse(status["configured"])


if __name__ == "__main__":
    unittest.main()
