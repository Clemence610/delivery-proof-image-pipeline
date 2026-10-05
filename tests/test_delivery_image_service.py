import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from delivery_image_service import DeliveryImageRequest, delivery_decision


def test_delivered_order_without_proof_is_held_for_follow_up() -> None:
    request = DeliveryImageRequest(
        shipment_id="SHIP-1042",
        event="delivered",
        filename="doorstep.jpg",
        content_type="image/jpeg",
        data_base64="aGVsbG8=",
        proof_of_delivery=False,
    )

    assert delivery_decision(request) == "needs-proof"
