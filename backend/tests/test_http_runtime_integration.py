from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from types import SimpleNamespace

from app.runtime.adapters.http import HttpAdapter


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/health")
            self.end_headers()
            return
        if self.path == "/large":
            body = b"x" * 5000
        else:
            body = b'{"status":"ok"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args):
        return


def test_real_http_health_status_redirect_and_body_limit():
    server = ThreadingHTTPServer(("127.0.0.1", 0), HealthHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        environment = SimpleNamespace(
            config_json={
                "health_endpoints": {
                    "ok": f"http://127.0.0.1:{port}/health",
                    "redirect": f"http://127.0.0.1:{port}/redirect",
                    "large": f"http://127.0.0.1:{port}/large",
                },
                "health_success_statuses": {"ok": [200], "redirect": [200], "large": [200]},
            }
        )
        ok = HttpAdapter().execute({"endpoint": "ok"}, environment)
        assert ok.status == "success" and ok.data["status_code"] == 200
        assert ok.data["resolved_ip"] == "127.0.0.1"

        redirect = HttpAdapter().execute({"endpoint": "redirect"}, environment)
        assert redirect.status == "failed" and redirect.data["status_code"] == 302

        large = HttpAdapter().execute({"endpoint": "large"}, environment)
        assert large.status == "success" and large.truncated is True
        assert len(large.data["body"]) <= 4096
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
