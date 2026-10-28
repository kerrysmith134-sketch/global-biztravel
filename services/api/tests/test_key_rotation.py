"""字段密钥轮换单元测试（不依赖数据库）。"""

from __future__ import annotations

from app.security.key_rotation import rotate_value
from cryptography.fernet import Fernet


def test_rotates_old_ciphertext_to_new_key() -> None:
    old = Fernet.generate_key().decode()
    new = Fernet.generate_key().decode()
    ciphertext = Fernet(old.encode()).encrypt(b"cto@example.com").decode()
    rotated, status = rotate_value(ciphertext, old_key=old, new_key=new)
    assert status == "rotated"
    assert rotated != ciphertext
    assert Fernet(new.encode()).decrypt(rotated.encode()).decode() == "cto@example.com"


def test_plaintext_is_skipped_by_default_or_encrypted_explicitly() -> None:
    new = Fernet.generate_key().decode()
    skipped, status = rotate_value("legacy@example.com", old_key=None, new_key=new)
    assert status == "plaintext_skipped"
    assert skipped == "legacy@example.com"

    encrypted, status = rotate_value(
        "legacy@example.com", old_key=None, new_key=new, encrypt_plaintext=True
    )
    assert status == "rotated"
    assert Fernet(new.encode()).decrypt(encrypted.encode()).decode() == "legacy@example.com"


def test_undecryptable_ciphertext_is_not_double_encrypted() -> None:
    other = Fernet.generate_key().decode()
    new = Fernet.generate_key().decode()
    ciphertext = Fernet(other.encode()).encrypt(b"secret").decode()
    rotated, status = rotate_value(ciphertext, old_key=Fernet.generate_key().decode(), new_key=new)
    assert status == "undecryptable"
    assert rotated == ciphertext
