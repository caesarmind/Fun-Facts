"""The real Fetcher against a local HTTP server: robots.txt, POST JSON, charset handling, cache."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from recipe_scraper.http import Fetcher, FetchError


class Handler(BaseHTTPRequestHandler):
    hits = []

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        Handler.hits.append(("GET", self.path))
        if self.path == "/robots.txt":
            self._send(200, "User-agent: *\nDisallow: /private/\n", "text/plain")
        elif self.path == "/page":
            self._send(200, "<h1>ბადრიჯანი ნიგვზით</h1>", "text/html")  # no charset on purpose
        else:
            self._send(404, "nope", "text/plain")

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Handler.hits.append(("POST", self.path, body))
        self._send(200, json.dumps({"result": {"id": body["find"]["id"], "title": "კაკაო"}}, ensure_ascii=False),
                   "application/json")


@pytest.fixture()
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    Handler.hits = []
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_fetcher(server, tmp_path):
    fx = Fetcher(cache_dir=tmp_path / "cache", delay=0)
    assert "ბადრიჯანი" in fx.get(server + "/page")  # UTF-8 even without a charset header
    with pytest.raises(FetchError, match="robots"):
        fx.get(server + "/private/x")
    assert fx.get_optional(server + "/missing") is None
    data = fx.post_json(server + "/api/recipe/", {"many": False, "find": {"id": 7}, "fields": []})
    assert data["result"] == {"id": 7, "title": "კაკაო"}

    before = len(Handler.hits)
    fx.get(server + "/page")  # served from cache
    fx.post_json(server + "/api/recipe/", {"many": False, "find": {"id": 7}, "fields": []})
    assert len(Handler.hits) == before

    offline = Fetcher(cache_dir=tmp_path / "cache", delay=0, offline=True)
    assert "ბადრიჯანი" in offline.get(server + "/page")
    with pytest.raises(FetchError, match="offline"):
        offline.get(server + "/other")
