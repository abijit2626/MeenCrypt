"""POST fish observations to the FISHRAND server - for the CV teammate.

Drop this into the fish-vision repo and call it whenever a new observation
window is ready:

    python3 fish_sender.py --url http://localhost:8000/api/observations \
        --json observations.json

or stream directly from your CV code:

    from fish_sender import send_observations
    send_observations(observations_dict)

The server validates the payload against the FISHRAND contract. Bad data
is rejected with a 422 and an explanation - nothing is ever executed from
the payload.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from typing import Any

DEFAULT_URL = "http://localhost:8000/api/observations"


def send_observations(payload: dict | list, url: str = DEFAULT_URL, *, timeout: float = 5.0) -> tuple[int, dict]:
    """POST an observation window; returns (http_status, response_json)."""
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(detail)
        except json.JSONDecodeError:
            parsed = {"detail": detail}
        return exc.code, parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fish_sender", description="send fish observations to FISHRAND")
    parser.add_argument("--json", required=True, help="path to observations JSON file")
    parser.add_argument("--url", default=DEFAULT_URL, help="server endpoint")
    args = parser.parse_args(argv)

    with open(args.json, encoding="utf-8") as handle:
        payload = json.load(handle)

    status, response = send_observations(payload, args.url)
    print(f"HTTP {status}: {json.dumps(response)[:400]}")
    return 0 if status == 200 else 1


if __name__ == "__main__":
    sys.exit(main())