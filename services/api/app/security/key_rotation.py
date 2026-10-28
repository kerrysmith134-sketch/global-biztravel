"""字段加密密钥轮换（纯函数，便于测试与脚本复用）。

轮换语义：

- 旧 key 能解开 ⇒ 用新 key 重新加密；
- 值不是密文（旧明文/外部写入）⇒ 默认跳过，只有显式 ``encrypt_plaintext=True`` 才加密为密文；
- 旧 key 解不开且看起来是密文 ⇒ 不改写并报告 ``undecryptable``，绝不把密文当明文二次加密。
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

_FERNET_PREFIX = "gAAAAA"


def rotate_value(
    value: str | None,
    *,
    old_key: str | None,
    new_key: str,
    encrypt_plaintext: bool = False,
) -> tuple[str | None, str]:
    if value is None or value == "":
        return value, "empty"
    new_fernet = Fernet(new_key.encode("utf-8"))
    plaintext: str | None = None
    if old_key:
        try:
            plaintext = Fernet(old_key.encode("utf-8")).decrypt(value.encode("utf-8")).decode("utf-8")
        except InvalidToken:
            plaintext = None
    if plaintext is None:
        if value.startswith(_FERNET_PREFIX):
            return value, "undecryptable"
        if not encrypt_plaintext:
            return value, "plaintext_skipped"
        plaintext = value
    return new_fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8"), "rotated"


__all__ = ["rotate_value"]
