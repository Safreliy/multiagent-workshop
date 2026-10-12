from __future__ import annotations

import asyncio
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx

from agents.runtime import build_model
from agents.types import AgentConfig


def _start_server(requests: list[str]) -> tuple[ThreadingHTTPServer, threading.Thread]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - callback name is defined by http.server
            requests.append(self.path)
            self.send_response(204)
            self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _stop_server(server: ThreadingHTTPServer, thread: threading.Thread) -> None:
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def _clear_proxy_environment(monkeypatch: Any) -> None:
    # Clear first because Windows environment keys are case-insensitive.
    for name in (
        "HTTP_PROXY",
        "http_proxy",
        "HTTPS_PROXY",
        "https_proxy",
        "ALL_PROXY",
        "all_proxy",
        "NO_PROXY",
        "no_proxy",
    ):
        monkeypatch.delenv(name, raising=False)


def test_standard_http_proxy_environment_routes_both_http_clients(monkeypatch: Any) -> None:
    requests: list[str] = []
    proxy, thread = _start_server(requests)
    proxy_url = f"http://127.0.0.1:{proxy.server_port}"
    _clear_proxy_environment(monkeypatch)
    monkeypatch.setenv("HTTP_PROXY", proxy_url)

    try:
        model = build_model(
            AgentConfig(
                model="proxy-test",
                api_key="test-key",
                base_url="http://model.invalid/v1",
            )
        )
        model_response = model.root_client._client.get("http://model.invalid/v1/models")
        model.root_client.close()
        asyncio.run(model.root_async_client.close())
        with httpx.Client(trust_env=True) as advisor_client:
            advisor_response = advisor_client.get("http://advisor.invalid/decision")
    finally:
        _stop_server(proxy, thread)

    assert model_response.status_code == 204
    assert advisor_response.status_code == 204
    assert requests == [
        "http://model.invalid/v1/models",
        "http://advisor.invalid/decision",
    ]


def test_no_proxy_bypasses_proxy_for_both_http_clients(monkeypatch: Any) -> None:
    proxy_requests: list[str] = []
    direct_requests: list[str] = []
    proxy, proxy_thread = _start_server(proxy_requests)
    direct, direct_thread = _start_server(direct_requests)
    _clear_proxy_environment(monkeypatch)
    monkeypatch.setenv("HTTP_PROXY", f"http://127.0.0.1:{proxy.server_port}")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    direct_url = f"http://127.0.0.1:{direct.server_port}"

    try:
        model = build_model(
            AgentConfig(model="proxy-test", api_key="test-key", base_url=direct_url)
        )
        model_response = model.root_client._client.get(f"{direct_url}/model")
        model.root_client.close()
        asyncio.run(model.root_async_client.close())
        with httpx.Client(trust_env=True) as advisor_client:
            advisor_response = advisor_client.get(f"{direct_url}/advisor")
    finally:
        _stop_server(proxy, proxy_thread)
        _stop_server(direct, direct_thread)

    assert model_response.status_code == 204
    assert advisor_response.status_code == 204
    assert proxy_requests == []
    assert direct_requests == ["/model", "/advisor"]
