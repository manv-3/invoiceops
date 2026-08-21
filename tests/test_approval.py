"""Tests for approval packet generation."""

import json
from decimal import Decimal
from pathlib import Path

from app.models.invoice import ApprovalPacket


def test_approval_packet_serializes_to_json(tmp_path: Path) -> None:
    packet = ApprovalPacket(
        invoice_id="inv-1",
        vendor_name="Acme Corp",
        invoice_number="INV-001",
        total=Decimal("1100.00"),
        currency="USD",
        payment_details="Bank: Test",
        approval_source="auto",
    )

    path = tmp_path / f"{packet.action_id}.json"
    path.write_text(
        json.dumps(packet.model_dump(mode="json"), indent=2, default=str),
        encoding="utf-8",
    )

    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["invoice_id"] == "inv-1"
    assert loaded["vendor_name"] == "Acme Corp"
    assert loaded["approval_source"] == "auto"


def test_approval_packet_unique_action_ids() -> None:
    p1 = ApprovalPacket(
        invoice_id="inv-1",
        vendor_name="A",
        invoice_number="1",
        total=Decimal("10"),
        currency="USD",
        approval_source="auto",
    )
    p2 = ApprovalPacket(
        invoice_id="inv-2",
        vendor_name="B",
        invoice_number="2",
        total=Decimal("20"),
        currency="USD",
        approval_source="reviewer",
    )
    assert p1.action_id != p2.action_id
