#!/usr/bin/env python3
"""Dependency-free server for the latency-hiding registration lab."""

from __future__ import annotations

import json
import os
import re
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "3000"))
WAN_DELAY_MS = int(os.getenv("WAN_DELAY_MS", "1200"))
PUBLIC_DIR = Path(__file__).resolve().parent / "public"
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def validate_registration(data: dict[str, Any]) -> dict[str, str]:
    """Return field-level validation errors for a registration request."""
    errors: dict[str, str] = {}
    name = str(data.get("name", "")).strip()
    email = str(data.get("email", "")).strip()
    password = str(data.get("password", ""))

    if len(name) < 2:
        errors["name"] = "Name must contain at least 2 characters."
    if not EMAIL_PATTERN.fullmatch(email):
        errors["email"] = "Enter a valid email address."
    if (
        len(password) < 8
        or not any(character.isalpha() for character in password)
        or not any(character.isdigit() for character in password)
    ):
        errors["password"] = (
            "Password must be 8+ characters and include a letter and a number."
        )

    return errors


class LabRequestHandler(BaseHTTPRequestHandler):
    """Serve the demo page and registration API."""

    server_version = "LatencyHidingLab/1.0"

    def send_json(self, status: HTTPStatus, body: dict[str, Any]) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802 - method name defined by BaseHTTPRequestHandler
        if self.path == "/":
            try:
                payload = (PUBLIC_DIR / "index.html").read_bytes()
            except OSError:
                self.send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"ok": False, "message": "Unable to load the page."},
                )
                return

            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)
            return

        if self.path == "/health":
            self.send_json(
                HTTPStatus.OK,
                {"ok": True, "wanDelayMs": WAN_DELAY_MS},
            )
            return

        self.send_json(HTTPStatus.NOT_FOUND, {"ok": False, "message": "Not found."})

    def do_POST(self) -> None:  # noqa: N802 - method name defined by BaseHTTPRequestHandler
        if self.path != "/api/register":
            self.send_json(
                HTTPStatus.NOT_FOUND,
                {"ok": False, "message": "Not found."},
            )
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            content_length = 0

        if content_length > 100_000:
            self.send_json(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {"ok": False, "message": "Request body is too large."},
            )
            return

        try:
            raw_body = self.rfile.read(content_length)
            data = json.loads(raw_body)
            if not isinstance(data, dict):
                raise ValueError("JSON body must be an object")
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            time.sleep(WAN_DELAY_MS / 1000)
            self.send_json(
                HTTPStatus.BAD_REQUEST,
                {"ok": False, "errors": {"form": "Invalid JSON."}},
            )
            return

        errors = validate_registration(data)
        time.sleep(WAN_DELAY_MS / 1000)

        if errors:
            self.send_json(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                {"ok": False, "errors": errors},
            )
            return

        name = str(data["name"]).strip()
        self.send_json(
            HTTPStatus.CREATED,
            {"ok": True, "message": f"Registration accepted for {name}."},
        )


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), LabRequestHandler)
    print(f"Latency-hiding lab running at http://{HOST}:{PORT}", flush=True)
    print(f"Simulated WAN delay: {WAN_DELAY_MS} ms per server request", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

