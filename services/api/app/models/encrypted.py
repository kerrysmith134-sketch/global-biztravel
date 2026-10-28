"""PII 字段级加密类型。

设计文档 v2.0 第 11.4 章。生产环境设置 ``GBT_FIELD_ENCRYPTION_KEY``（Fernet key）后，客户的
联系人姓名/邮箱/电话与现场笔记以密文落库；未设置时保持明文，方便本地开发与既有测试。

两个兼容性取舍：

1. **旧数据不会被读坏**：解密失败时原样返回。这样开启加密前写入的明文行仍然可读；
   新写入一律加密。回填脚本可以慢慢把旧行重写为密文。
2. **看起来已是密文的值不二次加密**：避免数据修复脚本或重复 flush 把密文再包一层。

注意：这是**应用层字段加密**，不是全盘加密；密钥泄露等于明文泄露。生产应把 key 放进 KMS/
密钥管理服务，并限制应用进程的读取权限（RL-05）。
"""

from __future__ import annotations

from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

from app.config import get_settings

_FERNET_PREFIX = "gAAAAA"


class EncryptedText(TypeDecorator):
    impl = Text
    cache_ok = True

    def __init__(self, key: str | None = None) -> None:
        super().__init__()
        self._explicit_key = key

    def _fernet(self) -> Fernet | None:
        key = self._explicit_key or get_settings().field_encryption_key
        if not key:
            return None
        try:
            return Fernet(key.encode("utf-8"))
        except (ValueError, TypeError) as exc:
            raise RuntimeError(
                "GBT_FIELD_ENCRYPTION_KEY 不是合法的 Fernet key（应为 base64 32 字节）"
            ) from exc

    def process_bind_param(self, value: Any, dialect: Any) -> str | None:  # noqa: ARG002
        if value is None:
            return None
        text = str(value)
        fernet = self._fernet()
        if fernet is None or text.startswith(_FERNET_PREFIX):
            return text
        return fernet.encrypt(text.encode("utf-8")).decode("utf-8")

    def process_result_value(self, value: Any, dialect: Any) -> str | None:  # noqa: ARG002
        if value is None:
            return None
        fernet = self._fernet()
        if fernet is None:
            return str(value)
        try:
            return fernet.decrypt(str(value).encode("utf-8")).decode("utf-8")
        except InvalidToken:
            # 加密开启前写入的明文：原样返回，保证平滑迁移。
            return str(value)


__all__ = ["EncryptedText"]
