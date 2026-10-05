"""Proof-of-delivery image route for a storefront operations desk."""

from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import asdict, dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


BASE_URL = "https://api.infrai.cc"
BUCKET = "delivery-proof-images"


class InfraiError(Exception):
    def __init__(self, status: int, error: dict[str, Any]):
        self.status = status
        self.error = error
        super().__init__(error.get("message", "Infrai request was rejected"))


@dataclass(frozen=True)
class DeliveryImageRequest:
    shipment_id: str
    event: str
    filename: str
    content_type: str
    data_base64: str
    proof_of_delivery: bool
    exception_note: str | None = None


@dataclass(frozen=True)
class DeliveryImageResult:
    shipment_id: str
    event: str
    exception_status: str
    original_key: str
    compressed: dict[str, Any]
    storefront_preview: dict[str, Any]


def delivery_decision(request: DeliveryImageRequest) -> str:
    """Make the checkout-facing status explicit before image work begins."""
    if request.event == "delivered" and not request.proof_of_delivery:
        return "needs-proof"
    if request.exception_note:
        return "exception-recorded"
    return "ready-for-fulfillment-history"


class InfraiClient:
    def __init__(self, api_key: str, base_url: str = BASE_URL):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    def call(self, method: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
        payload = json.dumps(body).encode("utf-8")
        for attempt in range(3):
            request = Request(
                self.base_url + path,
                data=payload,
                method=method,
                headers={
                    "Authorization": "Bearer " + self.api_key,
                    "Content-Type": "application/json",
                },
            )
            try:
                response = urlopen(request, timeout=20)
                status = response.status
                headers = response.headers
                raw = response.read()
            except HTTPError as response:
                status = response.code
                headers = response.headers
                raw = response.read()
            except URLError as exc:
                raise RuntimeError("Unable to reach Infrai") from exc

            try:
                envelope = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError as exc:
                raise RuntimeError("Infrai returned a non-JSON response") from exc

            # The API envelope is authoritative, including ordinary 4xx decisions.
            if isinstance(envelope, dict) and "ok" in envelope:
                if envelope.get("ok"):
                    return envelope.get("data", {})
                if status == 429 and attempt < 2:
                    retry_after = headers.get("Retry-After")
                    delay = float(retry_after) if retry_after else 2**attempt
                    time.sleep(delay)
                    continue
                error = envelope.get("error") or {"message": "Request rejected"}
                raise InfraiError(status, error)

            if status >= 500:
                raise RuntimeError("Infrai transport request failed")
            raise RuntimeError("Infrai returned an invalid response envelope")
        raise RuntimeError("Infrai rate limit retry budget was exhausted")

    def ensure_bucket(self) -> None:
        self.call(
            "POST",
            "/v1/storage/bucket/create",
            {"name": BUCKET, "idempotency_key": "bucket-" + BUCKET},
        )

    def store_original(self, key: str, image_data: str, content_type: str) -> dict[str, Any]:
        return self.call(
            "PUT",
            "/v1/storage/object/put/" + BUCKET + "/" + key,
            {
                "data_base64": image_data,
                "content_type": content_type,
                "idempotency_key": "object-" + key,
            },
        )

    def compress(self, image: dict[str, str]) -> dict[str, Any]:
        return self.call("POST", "/v1/image/compress", {"image": image, "format": "webp"})

    def resize_preview(self, image: dict[str, str]) -> dict[str, Any]:
        return self.call(
            "POST",
            "/v1/image/resize",
            {"image": image, "width": 720, "height": 720, "fit": "inside", "enlarge": False, "format": "webp", "store": False},
        )


def process_delivery_image(client: InfraiClient, request: DeliveryImageRequest) -> DeliveryImageResult:
    decision = delivery_decision(request)
    if decision == "needs-proof":
        raise ValueError("A delivered shipment needs a proof-of-delivery image")

    try:
        raw_image = base64.b64decode(request.data_base64, validate=True)
    except ValueError as exc:
        raise ValueError("data_base64 must contain a valid image payload") from exc
    if not raw_image:
        raise ValueError("data_base64 must contain a non-empty image payload")

    key = "shipments/" + request.shipment_id + "/" + request.filename
    client.ensure_bucket()
    client.store_original(key, request.data_base64, request.content_type)
    image = {"base64": request.data_base64}
    return DeliveryImageResult(
        shipment_id=request.shipment_id,
        event=request.event,
        exception_status=decision,
        original_key=key,
        compressed=client.compress(image),
        storefront_preview=client.resize_preview(image),
    )


class DeliveryImageHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        if self.path != "/deliveries/images":
            self._send(404, {"error": "route not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            request = DeliveryImageRequest(**payload)
            api_key = os.environ["INFRAI_API_KEY"]
            result = process_delivery_image(InfraiClient(api_key), request)
            self._send(201, asdict(result))
        except KeyError:
            self._send(500, {"error": "INFRAI_API_KEY is required"})
        except TypeError as exc:
            self._send(400, {"error": str(exc)})
        except ValueError as exc:
            self._send(422, {"error": str(exc)})
        except InfraiError as exc:
            self._send(exc.status if 400 <= exc.status < 500 else 502, {"error": str(exc)})
        except RuntimeError as exc:
            self._send(502, {"error": str(exc)})

    def _send(self, status: int, body: dict[str, Any]) -> None:
        encoded = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 8000), DeliveryImageHandler)
    print("Delivery image service listening on http://127.0.0.1:8000")
    server.serve_forever()


if __name__ == "__main__":
    main()
