#!/usr/bin/env python3
"""Probe private Home Connect oven endpoints with a mobile token."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib import error, parse, request


PRIVATE_API_HOST = "https://na.services.home-connect.com"
ACCOUNT_CAMERA_ACCEPTS = (
    "application/vnd.bsh.hca.v2+json",
    "application/vnd.bsh.hca.v1+json",
)
ROUTES = {
    "snapshot_latest_image": "/ui/app/v1/appliances/{ha_id}/oven/media/latest?type=image",
    "snapshot_latest_video": "/ui/app/v1/appliances/{ha_id}/oven/media/latest?type=video",
    "snapshot_gallery": "/ui/app/v1/appliances/{ha_id}/oven/media/gallery",
    "media_latest": "/api/media/v1/{ha_id}/media/latest",
    "media_latest_video": "/api/media/v1/{ha_id}/media/latest?type=video",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--token", help="Private mobile access token")
    parser.add_argument("--token-json", help="JSON file containing access_token")
    parser.add_argument("--ha-id", required=True, help="Private appliance haId")
    parser.add_argument("--output", help="Write JSON output to a file")
    parser.add_argument(
        "--allow-write-probes",
        action="store_true",
        help="Reserved for future write validation. No write probes run yet.",
    )
    args = parser.parse_args()

    token = args.token or _load_token(args.token_json)
    if not token:
        raise SystemExit("Missing --token or --token-json")

    results: dict[str, object] = {
        "ha_id": args.ha_id,
        "account_camera": [],
        "routes": {},
    }

    for accept in ACCOUNT_CAMERA_ACCEPTS:
        results["account_camera"].append(
            _request_json(
                "/account/camera",
                token,
                accept=accept,
            )
        )

    for name, route in ROUTES.items():
        endpoint = route.format(ha_id=args.ha_id)
        results["routes"][name] = _request_json(endpoint, token, accept="application/json")

    results["write_probe"] = {
        "requested": args.allow_write_probes,
        "status": "skipped",
        "reason": "No validated private write contract is implemented yet.",
    }

    output = json.dumps(results, indent=2, sort_keys=True)
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(output)
    else:
        print(output)
    return 0


def _load_token(token_json: str | None) -> str | None:
    if not token_json:
        return None
    payload = json.loads(Path(token_json).read_text())
    return payload.get("access_token")


def _request_json(endpoint: str, token: str, accept: str) -> dict[str, object]:
    url = f"{PRIVATE_API_HOST}{endpoint}"
    req = request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": accept,
        },
        method="GET",
    )
    try:
        with request.urlopen(req, timeout=30) as response:
            body = response.read()
            content_type = response.headers.get("Content-Type")
            decoded = _decode_body(body, content_type)
            return {
                "url": url,
                "status": response.status,
                "content_type": content_type,
                "body": decoded,
            }
    except error.HTTPError as err:
        body = err.read()
        content_type = err.headers.get("Content-Type")
        return {
            "url": url,
            "status": err.code,
            "content_type": content_type,
            "body": _decode_body(body, content_type),
        }
    except Exception as err:  # noqa: BLE001
        return {
            "url": url,
            "status": None,
            "error": str(err),
        }


def _decode_body(body: bytes, content_type: str | None):
    if not body:
        return ""
    if content_type and "json" in content_type:
        try:
            return json.loads(body.decode("utf-8"))
        except Exception:  # noqa: BLE001
            return body.decode("utf-8", errors="replace")
    return body.decode("utf-8", errors="replace")


if __name__ == "__main__":
    raise SystemExit(main())
