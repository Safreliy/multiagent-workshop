"""Serve the presentation and its local live demo with one command.

Run from the project root: uv run python -m presentation.serve
The command reuses either service when it is already running on its expected port.
"""

from __future__ import annotations

import http.client
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from presentation.live_demo import HERE, Handler as LiveHandler

HOST = "127.0.0.1"
STATIC_PORT = 8768
LIVE_PORT = 8769


def _service_present(port: int, path: str, marker: bytes) -> bool:
    connection = http.client.HTTPConnection(HOST, port, timeout=2)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        body = response.read(8192)
        if response.status != 200 or marker not in body:
            raise RuntimeError(f"Port {port} is occupied by a different service")
        return True
    except (ConnectionRefusedError, TimeoutError):
        return False
    except OSError as exc:
        raise RuntimeError(f"Cannot inspect port {port}: {exc}") from exc
    finally:
        connection.close()


def main() -> int:
    services: list[tuple[ThreadingHTTPServer, object]] = []
    try:
        if not _service_present(STATIC_PORT, "/index.html", b"deck.css"):
            static_handler = partial(SimpleHTTPRequestHandler, directory=str(HERE))
            static = ThreadingHTTPServer((HOST, STATIC_PORT), static_handler)
            static.daemon_threads = True
            services.append((static, None))
            print(f"Slides: http://{HOST}:{STATIC_PORT}/index.html", flush=True)
        else:
            print(f"Slides already running: http://{HOST}:{STATIC_PORT}/index.html", flush=True)

        if not _service_present(LIVE_PORT, "/live.html", b"SQL Agent Lab"):
            live = ThreadingHTTPServer((HOST, LIVE_PORT), LiveHandler)
            live.daemon_threads = True
            services.append((live, None))
            print(f"Live demo: http://{HOST}:{LIVE_PORT}/live.html", flush=True)
        else:
            print(f"Live demo already running: http://{HOST}:{LIVE_PORT}/live.html", flush=True)

        if not services:
            return 0
        from threading import Thread

        for index, (server, _) in enumerate(services):
            thread = Thread(target=server.serve_forever, name=f"presentation-server-{index}", daemon=True)
            thread.start()
            services[index] = (server, thread)
        print("Press Ctrl+C to stop the services started by this command.", flush=True)
        while all(thread and thread.is_alive() for _, thread in services):
            time.sleep(0.25)
        raise RuntimeError("A presentation service stopped unexpectedly")
    except KeyboardInterrupt:
        return 0
    finally:
        for server, thread in services:
            if thread and thread.is_alive():
                server.shutdown()
            server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
