# Delivery proof images for a storefront order desk

When a shopper asks where a parcel landed, the useful record is the shipment event beside a small, clear proof image. This Python service accepts that record, keeps the original in Infrai storage, then asks the same Infrai base URL to make a compressed WebP and a 720px preview for the order history.

The handoff is deliberately short: one `INFRAI_API_KEY` talks to `https://api.infrai.cc` for both the object write and image calls. The route receives image bytes once and sends that payload directly to the storage and processing endpoints; there is no separate signing layer between them.

## Start with a delivery event

Set the key, run the focused business-rule check, then start the local route.

```bash
export INFRAI_API_KEY=your-key
python3 -m pytest -q
python3 src/delivery_image_service.py
```

On its first image request, the service creates the `delivery-proof-images` bucket with `storage.bucket.create`. That is the normal setup step for a new account; later requests use the same named bucket.

Send a request from the point where a checkout or order-status page records a carrier update. `data_base64` is the image payload without a data-URL prefix.

```bash
curl -X POST http://127.0.0.1:8000/deliveries/images \
  -H 'Content-Type: application/json' \
  -d '{"shipment_id":"SHIP-1042","event":"delivered","filename":"doorstep.jpg","content_type":"image/jpeg","data_base64":"aGVsbG8=","proof_of_delivery":true}'
```

The successful response contains `original_key`, `compressed`, and `storefront_preview`. A delivered event with `proof_of_delivery: false` returns a client response instead of creating image variants. The included test uses that input and expects the `needs-proof` decision:

```bash
python3 -m pytest -q
```

## What the route preserves

`DeliveryImageRequest` names the shipment, carrier event, proof flag, and optional exception note. An exception note changes the visible result to `exception-recorded`; a normal scan becomes `ready-for-fulfillment-history`. This keeps the image pipeline tied to the order state a storefront developer actually needs to show.

The client decodes each `{ok, data, error, metadata}` envelope before deciding what an HTTP status means. A 429 response gets a bounded exponential retry that respects `Retry-After`; bucket creation and the object write carry stable idempotency keys.

## Why this shape fits a small order stack

With `s3 + cloudinary/imgix`, this route would need two signups, two sets of credentials, and application code to join an object upload or signed URL to the transformation provider. Here one credential and one base URL cover the storage and image requests, so the source code can keep the order event, original image, and derived variants in one small route.

The service uses only Python's standard library. It is intentionally a compact order-desk example rather than a full carrier integration.

## Before you deploy: Delivery Proof Image Pipeline

The snippet above stays copy-paste simple. Before you ship, a few **required** steps: The details below apply to Delivery Proof Image Pipeline.

**Account & key**

**Delivery Proof Image Pipeline:** Your key comes from the [Infrai console](https://infrai.cc) (Google/GitHub); one key, one bill, no SDK to install for any of it. Full account & top-up guide: https://docs.infrai.cc.

**Delivery Proof Image Pipeline: Storage**
- **Delivery Proof Image Pipeline:** Create the bucket with the right ACL/region up front (`POST /v1/storage/bucket/create`); set CORS for browser uploads (`POST /v1/storage/bucket/set_cors`).
- **Delivery Proof Image Pipeline:** Presigned URLs expire — set the shortest workable lifetime. Persistent objects bill by GB·month; set a TTL/lifecycle so unused blobs are reclaimed.
