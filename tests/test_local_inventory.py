import importlib.util
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/local_inventory.py"
spec = importlib.util.spec_from_file_location("local_inventory", SCRIPT)
preview = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preview)


class StaticPreviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "index.html").write_bytes(b"fictional site")
        (root / "i18n-en.js").write_bytes(b"fictional localization")
        (root / "data").mkdir()
        (root / "data/index.json").write_text('[{"id":"v5.3"},{"id":"v4.2"}]')
        (root / "data/v5.3.json").write_bytes(b'{"public":true}')
        for name in ("batches.json", "v5.4.json"):
            (root / "data" / name).write_bytes(b"fictional private stock")
        (root / "data/v4.2.json").symlink_to(root / "data/v5.4.json")
        (root / "scripts").mkdir()
        (root / "scripts/local_inventory.py").write_bytes(b"fictional source")
        self.host = "127.0.0.1:8000"
        self.server = SimpleNamespace(site_root=root.resolve(), server_port=8000, allowed_hosts={self.host})

    def request(self, method, path, body=b"", headers=None):
        headers = {"Host": self.host, "Content-Length": str(len(body)), **(headers or {})}
        head = f"{method} {path} HTTP/1.1\r\n" + "".join(f"{name}: {value}\r\n" for name, value in headers.items())
        request = io.BytesIO(head.encode() + b"\r\n" + body)
        output = io.BytesIO()
        handler = preview.LocalInventoryHandler.__new__(preview.LocalInventoryHandler)
        handler.rfile, handler.wfile, handler.server = request, output, self.server
        handler.handle_one_request()
        response_head, response_body = output.getvalue().split(b"\r\n\r\n", 1)
        status, *raw_headers = response_head.decode().split("\r\n")
        return int(status.split()[1]), dict(line.split(": ", 1) for line in raw_headers), response_body

    def test_serves_static_page_language_and_registered_board(self):
        for path, content in (
            ("/", b"fictional site"),
            ("/index.html", b"fictional site"),
            ("/i18n-en.js", b"fictional localization"),
            ("/data/index.json", b'[{"id":"v5.3"},{"id":"v4.2"}]'),
            ("/data/v5.3.json?cache=bust", b'{"public":true}'),
        ):
            with self.subTest(path=path):
                status, headers, body = self.request("GET", path)
                self.assertEqual((status, body), (200, content))
                head_status, head_headers, head_body = self.request("HEAD", path)
                self.assertEqual((head_status, head_headers["Content-Length"], head_body),
                                 (200, headers["Content-Length"], b""))

    def test_private_data_and_source_cannot_be_served(self):
        for path in ("/data/batches.json", "/data/v5.4.json", "/data/v4.2.json", "/data/../index.html",
                     "/data/%2e%2e/index.html", "/scripts/local_inventory.py", "/data/"):
            with self.subTest(path=path):
                self.assertEqual(self.request("GET", path)[0], 404)

    def test_old_inventory_post_routes_are_unavailable(self):
        headers = {"Origin": f"http://{self.host}", "Content-Type": "application/octet-stream"}
        for path, body in (("/__local_inventory/history", b""),
                           ("/__local_inventory/parse", b"model,stock\nA,1\n")):
            with self.subTest(path=path):
                status, _, response = self.request("POST", path, body, headers)
                self.assertIn(status, (404, 405, 501))
                self.assertNotIn(b"fictional", response)

    def test_rejects_external_host_and_cross_site_requests(self):
        self.assertEqual(self.request("GET", "/", headers={"Host": "example.invalid"})[0], 403)
        self.assertEqual(self.request("GET", "/", headers={"Sec-Fetch-Site": "cross-site"})[0], 403)


if __name__ == "__main__":
    unittest.main()
