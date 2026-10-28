"""运行时配置。一切外部可变量都从这里读，禁止在代码里硬编码（红线 RL-05）。

所有配置项均可通过 GBT_ 前缀的环境变量或仓库根目录的 .env 覆盖。
"""

from __future__ import annotations

from functools import lru_cache
from uuid import UUID

from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_DEV_ORG = UUID("11111111-1111-1111-1111-111111111111")
DEFAULT_DEV_USER = UUID("22222222-2222-2222-2222-222222222222")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GBT_", env_file=".env", extra="ignore")

    env: str = "local"

    # 应用连接：gbt_app（非表 owner → RLS 必然生效）
    database_url: str = "postgresql+psycopg://gbt_app:gbt_app_pw@localhost:55432/gbt_os"
    # 迁移连接：gbt_owner（表 owner，配合 FORCE RLS）
    migration_database_url: str = "postgresql+psycopg://gbt_owner:gbt_owner_pw@localhost:55432/gbt_os"

    dev_auth: bool = True
    dev_org_id: UUID = DEFAULT_DEV_ORG
    dev_user_id: UUID = DEFAULT_DEV_USER

    # 规划引擎
    planning_solver: str = "ortools"          # ortools | heuristic
    solver_budget_seconds: int = 10
    candidate_limit: int = 8
    # 每日停靠上限。默认值取自设计文档第 6.4 章示例的 `constraints.max_stops_per_day`。
    # 必须走配置而不是硬编码（红线 RL-05）：它是业务约束，不是实现细节。
    max_stops_per_day: int = 4
    # 交通估算 provider（PRD §5.1 / AT-003）：offline-haversine | unavailable（验证降级）
    travel_provider: str = "offline"

    # 主动式运行时（第 8.4 章）
    suggestion_cooldown_hours: int = 24
    daily_suggestion_cap: int = 3
    suggestion_confidence_threshold: float = 0.6
    # 调度扫描间隔：Worker 每轮都会检查，但真正扫库按这个间隔节流。
    proactive_scan_interval_seconds: int = 60
    # 签证/证件核验的提前量（设计文档 §8.1 的 T-72h）。
    proactive_visa_lookahead_hours: int = 72
    # 误报回收：每条被驳回的同类建议扣多少置信度，以及扣分上限。
    proactive_dismissal_penalty: float = 0.05
    proactive_max_feedback_penalty: float = 0.3
    proactive_feedback_window_days: int = 90

    # PII 字段加密（Fernet key，base64 32 字节）。未设置时本地开发明文，生产必须设置。
    field_encryption_key: str | None = None
    # 数据留存：超过天数的现场笔记与记忆正文自动脱敏。
    retention_days: int = 365
    retention_interval_seconds: int = 86400

    # 记忆层（第 9 章）
    memory_embedding_dim: int = 256
    memory_embedding_provider: str = "hash"   # hash | openai_compatible
    memory_embedding_base_url: str | None = None
    memory_embedding_model: str = "text-embedding-3-small"
    memory_embedding_api_key: str | None = None
    memory_time_decay_half_life_days: int = 90
    # 行程完成后复盘的扫描间隔（秒）。复盘本身按 source_ref 幂等，扫描频率只影响时延。
    memory_review_interval_seconds: int = 300

    # 指标快照间隔（秒）。同窗口重跑覆盖，不影响正确性。
    metrics_snapshot_interval_seconds: int = 3600

    # 推送通道（第 8.6 章）：本地默认 inapp（写入通知列表），log 只打日志，apns 需证书。
    push_provider: str = "inapp"              # inapp | log | smtp | apns
    push_max_attempts: int = 3

    # SMTP（push_provider=smtp）
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    smtp_use_tls: bool = True

    # APNs（push_provider=apns）：token-based auth（.p8）
    apns_key_id: str | None = None
    apns_team_id: str | None = None
    apns_private_key: str | None = None       # PEM 内容或 .p8 文件路径
    apns_topic: str | None = None             # bundle id
    apns_use_sandbox: bool = True

    # LLM 网关（第 7 章）
    llm_provider: str = "stub"                # stub | openai_compatible
    llm_temperature: float = 0.0
    llm_seed: int = 0
    llm_max_tokens: int = 1024
    llm_base_url: str | None = None
    llm_model: str = "gbt-local"
    llm_api_key: str | None = None            # 只从环境变量注入，绝不写文件
    # 成本估算护栏：每千 token 单价（本地 stub 默认 0）。仅用于记录，不做硬性拦截。
    llm_cost_per_1k_tokens: float = 0.0

    # Worker / 可靠性
    worker_poll_seconds: int = 2
    job_lease_seconds: int = 60
    outbox_batch_size: int = 50
    retry_base_seconds: int = 2
    retry_max_seconds: int = 300
    max_attempts: int = 5

    @property
    def is_local(self) -> bool:
        return self.env in {"local", "test"}


@lru_cache
def get_settings() -> Settings:
    return Settings()