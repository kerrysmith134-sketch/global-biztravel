"""S3 receipt storage adapter pagination and HEAD tests."""

from __future__ import annotations

from datetime import UTC, datetime
import pytest

from app.services.receipt_storage_service import (
    ReceiptStorageUnavailable,
    S3ReceiptStorage,
    StoredReceiptObject,
)


class FakePaginator:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def paginate(self, **kwargs):
        self.calls.append(kwargs)
        return iter(self.pages)


class FakeS3Client:
    def __init__(self, *, pages=None, head_response=None, head_error=None):
        self.paginator = FakePaginator(pages or [])
        self.head_response = head_response
        self.head_error = head_error
        self.head_calls = []

    def get_paginator(self, operation):
        assert operation == "list_objects_v2"
        return self.paginator

    def head_object(self, **kwargs):
        self.head_calls.append(kwargs)
        if self.head_error is not None:
            raise self.head_error
        return self.head_response


def build_storage(client: FakeS3Client) -> S3ReceiptStorage:
    storage = S3ReceiptStorage.__new__(S3ReceiptStorage)
    storage.bucket = "private-receipts"
    storage.client = client
    return storage


def test_s3_list_objects_paginates_and_only_requests_tenant_prefix() -> None:
    first_time = datetime(2026, 11, 24, 10, 0, tzinfo=UTC)
    second_time = datetime(2026, 11, 24, 11, 0, tzinfo=UTC)
    client = FakeS3Client(
        pages=[
            {
                "Contents": [
                    {
                        "Key": "receipts/11111111-1111-1111-1111-111111111111/"
                        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/"
                        "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
                        "Size": 20,
                        "LastModified": first_time,
                    }
                ],
                "IsTruncated": True,
            },
            {
                "Contents": [
                    {
                        "Key": "receipts/11111111-1111-1111-1111-111111111111/"
                        "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb/"
                        "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
                        "Size": 30,
                        "LastModified": second_time,
                    }
                ],
                "IsTruncated": False,
            },
        ]
    )
    storage = build_storage(client)

    objects = storage.list_objects("receipts/11111111-1111-1111-1111-111111111111/")

    assert len(objects) == 2
    assert all(isinstance(item, StoredReceiptObject) for item in objects)
    assert [item.size for item in objects] == [20, 30]
    assert client.paginator.calls == [
        {
            "Bucket": "private-receipts",
            "Prefix": "receipts/11111111-1111-1111-1111-111111111111/",
        }
    ]


def test_s3_list_objects_rejects_bucket_wide_or_invalid_prefix() -> None:
    storage = build_storage(FakeS3Client())

    with pytest.raises(ValueError):
        storage.list_objects("receipts/")
    with pytest.raises(ValueError):
        storage.list_objects("receipts/not-a-uuid/")

    with pytest.raises(ValueError):
        storage.stat_object(
            "receipts/11111111-1111-1111-1111-111111111111/"
            "not-a-trip-uuid/"
            "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
        )


def test_s3_stat_object_returns_head_metadata() -> None:
    modified = datetime(2026, 11, 25, 12, 30, tzinfo=UTC)
    client = FakeS3Client(
        head_response={
            "ContentLength": 42,
            "LastModified": modified,
        }
    )
    storage = build_storage(client)
    key = (
        "receipts/11111111-1111-1111-1111-111111111111/"
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/"
        "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
    )

    actual = storage.stat_object(key)

    assert actual == StoredReceiptObject(key=key, size=42, last_modified=modified)
    assert client.head_calls == [{"Bucket": "private-receipts", "Key": key}]


def test_s3_stat_object_maps_not_found_to_none() -> None:
    class FakeClientError(Exception):
        def __init__(self):
            super().__init__("not found")
            self.response = {
                "Error": {"Code": "404"},
                "ResponseMetadata": {"HTTPStatusCode": 404},
            }

    client = FakeS3Client(head_error=FakeClientError())
    storage = build_storage(client)
    key = (
        "receipts/11111111-1111-1111-1111-111111111111/"
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/"
        "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
    )

    assert storage.stat_object(key) is None


def test_s3_stat_object_does_not_hide_permission_errors() -> None:
    class FakeClientError(Exception):
        def __init__(self):
            super().__init__("access denied")
            self.response = {
                "Error": {"Code": "AccessDenied"},
                "ResponseMetadata": {"HTTPStatusCode": 403},
            }

    client = FakeS3Client(head_error=FakeClientError())
    storage = build_storage(client)
    key = (
        "receipts/11111111-1111-1111-1111-111111111111/"
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/"
        "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
    )

    with pytest.raises(ReceiptStorageUnavailable) as captured:
        storage.stat_object(key)
    assert "无法检查 S3" in str(captured.value)
