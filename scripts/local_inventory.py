#!/usr/bin/env python3
"""在本机回环地址静态预览贴片助手。"""

import argparse
import json
import re
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PORT = 8000
STATIC_ASSETS = {
    "/": "index.html",
    "/index.html": "index.html",
    "/i18n-en.js": "i18n-en.js",
    "/data/versions.json": "data/versions.json",
    "/data/index.json": "data/index.json",
}
STATIC_BOARD = re.compile(r"/data/(v\d+(?:\.\d+)*(?:-(?:pwr|nopwr))?)\.json")
MEDIA_TYPES = {".html": "text/html", ".js": "text/javascript", ".json": "application/json"}


class LocalInventoryHandler(BaseHTTPRequestHandler):
    timeout = 10

    def log_message(self, *args):
        pass

    def respond(self, status, content, content_type="application/json", *, headers_only=False):
        self.send_response(status)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.end_headers()
        if not headers_only:
            self.wfile.write(content)

    def respond_json(self, status, data, *, headers_only=False):
        self.respond(status, json.dumps(data, ensure_ascii=False).encode(), headers_only=headers_only)

    def trusted_host(self):
        hosts = self.headers.get_all("Host") or []
        return len(hosts) == 1 and hosts[0].lower() in self.server.allowed_hosts

    def serve_static(self, headers_only=False):
        if not self.trusted_host() or self.headers.get("Sec-Fetch-Site") == "cross-site":
            self.respond_json(403, {"error": "此服务仅接受本机访问。"}, headers_only=headers_only)
            return
        request_path = self.path.split("?", 1)[0]
        root = self.server.site_root
        relative = STATIC_ASSETS.get(request_path)
        try:
            board = STATIC_BOARD.fullmatch(request_path)
            if board:
                index = root / STATIC_ASSETS["/data/index.json"]
                if index.resolve() == index:
                    entries = json.loads(index.read_bytes())
                    if isinstance(entries, list) and any(
                            isinstance(entry, dict) and entry.get("id") == board[1] for entry in entries):
                        relative = request_path[1:]
            if relative is not None:
                file = root / relative
                if file.resolve() == file and file.is_file():
                    self.respond(200, file.read_bytes(), MEDIA_TYPES[file.suffix], headers_only=headers_only)
                    return
        except (OSError, RuntimeError, ValueError):
            pass
        self.respond_json(404, {"error": "页面或数据不存在。"}, headers_only=headers_only)

    def do_GET(self):
        self.serve_static()

    def do_HEAD(self):
        self.serve_static(headers_only=True)


class LocalInventoryServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        # HTTP 错误不回显本机目录。
        pass


def create_server(port=DEFAULT_PORT, root=ROOT, *, host=None):
    address = socket.getaddrinfo(host, 0, socket.AF_INET, socket.SOCK_STREAM)[0][4][0]
    if not ip_address(address).is_loopback:
        raise ValueError("预览服务只能绑定到本机回环地址")
    site_root = root.resolve()
    server = LocalInventoryServer((address, port), LocalInventoryHandler)
    server.site_root = site_root
    hostnames = {address, host.lower()} if host else {address}
    server.allowed_hosts = {f"{name}:{server.server_port}" for name in hostnames}
    if server.server_port == 80:
        server.allowed_hosts.update(hostnames)
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="预览端口，默认 8000")
    parser.add_argument("--host", help="本机回环主机名或 IPv4 回环地址，默认由系统选择")
    args = parser.parse_args()
    try:
        with create_server(args.port, host=args.host) as server:
            host = args.host or server.server_address[0]
            print(f"本机贴片助手已启动：http://{host}:{server.server_port}/", flush=True)
            server.serve_forever()
    except (OSError, OverflowError, ValueError):
        parser.exit(1, "无法启动本机贴片助手，请检查回环地址和端口。\n")
    except KeyboardInterrupt:
        return


if __name__ == "__main__":
    main()
