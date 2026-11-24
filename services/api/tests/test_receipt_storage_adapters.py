from __future__ import annotations

import sys
from datetime import UTC, datetime
from types import SimpleNamespace

from app.services.receipt_storage_service import S3ReceiptStorage


def test_s3_receipt_listing_paginates_and_filters_noncanonical_keys(monkeypatch) -> None:
    organization_id = "11111111-1111-1111-1111-111111111111"
    prefix = f"receipts/{organization_id}/"
    valid_key_one = f"{prefix}22222222-2222-2222-2222-222222222222"
    valid_key_two = f"{prefix}33333333-3333-3333-3333-333333333333"
    now = datetime.now(UTC)

    class FakePaginator:
        def paginate(self, *, Bucket: str, Prefix: str):  # noqa: N803
            assert Bucket == "private-receipts"
            assert Prefix == prefix
            return [
                {
                    "Contents": [
                        {"Key": valid_key_one, "LastModified": now},
                        {"Key": f"{prefix}not-a-uuid", "LastModified": now},
                    ]
                },
                {
                    "Contents": [
                        {"Key": valid_key_two, "LastModified": now},
                        {
                            "Key": "receipts/33333333-3333-3333-3333-333333333333/"
                            "44444444-4444-4444-4444-444444444444/"
                            "55555555-5555-5555-5555-555555555555",
                            "LastModified": now,
                        },
                    ]
                },
            ]

    class FakeS3Client:
        def get_paginator(self, operation: str) -> FakePaginator:
            assert operation == "list_objects_v2"
            return FakePaginator()

    fake_client = FakeS3Client()
    fake_boto3 = SimpleNamespace(client=lambda **_kwargs: fake_client)
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)

    storage = S3ReceiptStorage(
        bucket="private-receipts",
        region="us-east-1",
        endpoint_url=None,
        access_key="test-access",
        secret_key="test-secret",
    )

    listed = storage.list_objects(prefix)

    assert [item.key for item in listed] == [valid_key_one, valid_key_two]
    assert all(item.last_modified.tzinfo is not None for item in listed)
