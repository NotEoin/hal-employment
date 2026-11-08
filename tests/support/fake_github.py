"""A made-up GitHub for tests: its API served from 127.0.0.1, and its projects cloned from a temporary folder.

`FakeGitHub()` is a context manager. Inside it `web.API_BASE` points at a local server answering the paths in
`api` (a path with its query string first, then the path alone; a value may be a function of the request's
headers), and `github.CLONE_BASE` points at `file://<folder>`, where `project(owner, name, ...)` makes a
made-up project with `support.repos.make`. Nothing reaches the internet.
"""
from __future__ import annotations

import json
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from jobhunt.ingest import web
from jobhunt.sections import github
from support import repos


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.server.seen.append(self.path)
        answer = self.server.api.get(self.path) or self.server.api.get(self.path.split("?", 1)[0])
        if callable(answer):
            answer = answer(dict(self.headers))
        status, body = answer if answer is not None else (404, {"message": "Not Found"})
        data = json.dumps(body).encode()
        self.send_response(status)
        for k, v in self.server.headers.get(status, {}).items():
            self.send_header(k, v)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args) -> None:
        pass


def repo_json(owner: str, name: str, **more) -> dict:
    """GitHub's description of a made-up repository."""
    return {"name": name, "owner": {"login": owner}, "html_url": f"{web.GITHUB_WEB}/{owner}/{name}",
            "description": more.pop("description", f"The made-up {name}"), "topics": more.pop("topics", []),
            "license": more.pop("license", None), "pushed_at": more.pop("pushed_at", "2024-06-01T10:00:00Z"),
            "fork": more.pop("fork", False), "private": more.pop("private", False), **more}


class FakeGitHub:
    """The made-up GitHub; `api` maps paths to (status, JSON body), `seen` lists the paths asked for."""

    def __enter__(self) -> FakeGitHub:
        self._tmp = tempfile.TemporaryDirectory(prefix="hal-fake-github-")
        self.folder = Path(self._tmp.name)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.httpd.api, self.httpd.seen, self.httpd.headers = {}, [], {}
        self.api, self.seen, self.headers = self.httpd.api, self.httpd.seen, self.httpd.headers
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()
        self._patches = [mock.patch.object(web, "API_BASE", f"http://127.0.0.1:{self.httpd.server_port}"),
                         mock.patch.object(github, "CLONE_BASE", f"file://{self.folder}")]
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc) -> None:
        for p in reversed(self._patches):
            p.stop()
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
        self._tmp.cleanup()

    def project(self, owner: str, name: str, files: dict | None = None, commits: list | None = None, **about) -> dict:
        """A made-up project to clone, and GitHub's answer for it at `/repos/<owner>/<name>`; returns that."""
        repos.make(self.folder / owner / f"{name}.git", files or {"README.md": f"# {name}\n"}, commits)
        data = repo_json(owner, name, **about)
        self.api[f"/repos/{owner}/{name}"] = (200, data)
        return data
