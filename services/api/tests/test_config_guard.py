"""配置安全护栏（P0-3）：非本地环境禁止 dev_auth。"""

from __future__ import annotations

import pytest
from app.config import Settings


def test_production_rejects_dev_auth():
    with pytest.raises(ValueError, match="GBT_DEV_AUTH"):
        Settings(env="production", dev_auth=True)


def test_staging_rejects_dev_auth():
    with pytest.raises(ValueError, match="GBT_DEV_AUTH"):
        Settings(env="staging", dev_auth=True)


def test_local_and_docker_allow_dev_auth():
    assert Settings(env="local", dev_auth=True).dev_auth is True
    assert Settings(env="docker", dev_auth=True).dev_auth is True


def test_production_without_dev_auth_starts():
    settings = Settings(env="production", dev_auth=False)
    assert settings.dev_auth is False
