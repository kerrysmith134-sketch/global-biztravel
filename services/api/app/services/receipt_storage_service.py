"""私有票据对象存储适配器：本地原子文件存储与 S3 兼容对象存储。"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any, Protocol

from app.config import get_settings


class ReceiptStorageUnavailable(RuntimeError):
    """私有票据存储暂时不可用。"""


class ReceiptStorage(Protocol):
    provider: str

    def put(self, key: str, content: bytes, content_type: str) -> None: ...

    def get(self, key: str) -> bytes: ...

    def delete(self, key: str) -> None: ...


_KEY_PATTERN = re.compile(
    r"^receipts/[0-9a-f-]{36}/[0-9a-f-]{36}/[0-9a-f-]{36}$",
    re.IGNORECASE,
)


def _validate_key(key: str) -> None:
    if not _KEY_PATTERN.fullmatch(key):
        raise ValueError("invalid receipt storage key")


class FilesystemReceiptStorage:
    """只写入配置的私有根目录；临时文件与最终文件位于同一目录以保证原子替换。"""

    provider = "filesystem"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            self.root.chmod(0o700)
        except OSError:
            pass

    def _path(self, key: str) -> Path:
        _validate_key(key)
        path = (self.root / key).resolve()
        if self.root not in path.parents:
            raise ValueError("receipt storage key escapes private root")
        return path

    def put(self, key: str, content: bytes, content_type: str) -> None:  # noqa: ARG002
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            target.parent.chmod(0o700)
        except OSError:
            pass
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", prefix=".receipt-", dir=target.parent, delete=False
            ) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write(content)
                temporary.flush()
                os.fsync(temporary.fileno())
            temporary_path.chmod(0o600)
            os.replace(temporary_path, target)
        except OSError as exc:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise ReceiptStorageUnavailable("无法保存原始票据") from exc

    def get(self, key: str) -> bytes:
        target = self._path(key)
        try:
            return target.read_bytes()
        except FileNotFoundError as exc:
            raise ReceiptStorageUnavailable("原始票据文件不存在") from exc
        except OSError as exc:
            raise ReceiptStorageUnavailable("无法读取原始票据") from exc

    def delete(self, key: str) -> None:
        target = self._path(key)
        try:
            target.unlink(missing_ok=True)
        except OSError as exc:
            raise ReceiptStorageUnavailable("无法删除原始票据临时对象") from exc


class S3ReceiptStorage:
    """S3-compatible private bucket adapter. No public ACL or presigned URL is issued."""

    provider = "s3"

    def __init__(
        self,
        *,
        bucket: str,
        region: str,
        endpoint_url: str | None,
        access_key: str,
        secret_key: str,
    ) -> None:
        try:
            import boto3
        except ImportError as exc:  # pragma: no cover - guarded by runtime dependency
            raise ReceiptStorageUnavailable("S3 存储适配器缺少 boto3 依赖") from exc
        self.bucket = bucket
        self.client: Any = boto3.client(
            "s3",
            region_name=region,
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )

    def put(self, key: str, content: bytes, content_type: str) -> None:
        _validate_key(key)
        try:
            self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=content,
                ContentType=content_type,
                Metadata={"storage-class": "private-receipt"},
                ServerSideEncryption="AES256",
            )
        except Exception as exc:
            raise ReceiptStorageUnavailable("无法写入 S3 票据对象") from exc

    def get(self, key: str) -> bytes:
        _validate_key(key)
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            return response["Body"].read()
        except Exception as exc:
            raise ReceiptStorageUnavailable("无法读取 S3 票据对象") from exc

    def delete(self, key: str) -> None:
        _validate_key(key)
        try:
            self.client.delete_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            raise ReceiptStorageUnavailable("无法删除 S3 票据对象") from exc


def get_receipt_storage(provider: str | None = None) -> ReceiptStorage:
    settings = get_settings()
    selected = provider or settings.receipt_storage_provider
    if selected == "filesystem":
        return FilesystemReceiptStorage(settings.receipt_storage_root)
    if selected == "s3":
        if not all(
            (settings.receipt_s3_bucket, settings.receipt_s3_access_key, settings.receipt_s3_secret_key)
        ):
            raise ReceiptStorageUnavailable("S3 票据存储未配置完整")
        return S3ReceiptStorage(
            bucket=settings.receipt_s3_bucket,
            region=settings.receipt_s3_region,
            endpoint_url=settings.receipt_s3_endpoint_url,
            access_key=settings.receipt_s3_access_key,
            secret_key=settings.receipt_s3_secret_key,
        )
    raise ReceiptStorageUnavailable(f"不支持的票据存储 Provider：{selected}")


__all__ = [
    "FilesystemReceiptStorage",
    "ReceiptStorage",
    "ReceiptStorageUnavailable",
    "S3ReceiptStorage",
    "get_receipt_storage",
]
