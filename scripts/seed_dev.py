#!/usr/bin/env python
"""开发种子数据脚本（可重复运行）。

为什么需要它：验收原则明确要求"必须在真实客户端、后端和数据库之间完成，不能以静态页面、
模拟 API 返回值或预置演示数据代替"。也就是说 iOS 客户端与后续端到端测试必须打到一个**真的
有数据**的 PostgreSQL 上，而不是打桩。本脚本就负责把这份"真数据"造进库里。

设计要点：

- **幂等**：所有实体都按自然键先查后建（``resolve_or_create_*`` / 按名查 Trip / 按
  (trip, place, title) 查 Visit）。反复运行不会产生重复客户、重复地点或重复拜访，
  因此可以安全地放进任何"首次启动"流程。
- **两个租户**：租户 A 是主用演示数据（``11111111-…``，与 ``.env`` 的 ``GBT_DEV_ORG_ID``
  一致）；租户 B 是**刻意存在**的第二租户，用于验证 RLS 真的在拦——用 A 的身份查 B 的
  Trip 必须查不到（验收场景 AT-S01）。
- **身份表走 owner、业务表走 app**：身份三表是 ``ENABLE`` 但不 ``FORCE`` 的（一次性开通
  属于特权引导操作），所以用 ``owner_session()``；业务表全部 ``FORCE``，必须走
  ``org_session(org)`` 并在事务内绑定 ``app.current_org``——种子数据本身就顺带验证了
  这条隔离路径是通的。
- **附带生成一次性的 Excel 夹具**：``docs/acceptance-tests/fixtures/sample-customers.xlsx``，
  刻意混入 8 类"脏数据"，用来验证导入器的净空报告确实能逐类识别（见文件末尾的
  ``build_fixture_rows`` 注释）。**夹具里的会议日期相对"今天"生成**（+2/+3/+4 天）：
  日期写死会让夹具过期，导入的拜访落在过去 → 规划器如实判不可行 → 链式/导入用例
  在规划阶段必挂且根因离现场很远。夹具是生成产物、不入版本库（见 ``.gitignore``）。

用法::

    .venv/bin/python scripts/seed_dev.py              # 造数据 + 生成 Excel 夹具
    .venv/bin/python scripts/seed_dev.py --no-fixture # 只造数据
    .venv/bin/python scripts/seed_dev.py --verify     # 造完数据后打印各租户的可见行数
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "services" / "api"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from app.api.schemas import TripCreate, VisitCreate  # noqa: E402
from app.db import org_session, owner_session  # noqa: E402
from app.models.geo import Customer, Place  # noqa: E402
from app.models.identity import ROLES, Membership, Organization, User  # noqa: E402
from app.models.trip import ImportBatch, Trip, Visit  # noqa: E402
from app.services import (  # noqa: E402
    customer_service,
    place_service,
    trip_service,
    visit_service,
)
from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

# --------------------------------------------------------------------- 固定标识
ORG_A = UUID("11111111-1111-1111-1111-111111111111")  # 与 .env 的 GBT_DEV_ORG_ID 一致
ORG_B = UUID("33333333-3333-3333-3333-333333333333")  # 第二租户，专供隔离验收

USER_A = UUID("22222222-2222-2222-2222-222222222222")  # 与 .env 的 GBT_DEV_USER_ID 一致
USER_A2 = UUID("55555555-5555-5555-5555-555555555555")
USER_B = UUID("44444444-4444-4444-4444-444444444444")

ORG_A_SLUG = "anari-energy"
ORG_B_SLUG = "northwind-trading"

TRIP_A_NAME = "2026 Q4 欧洲多国商务出行"
TRIP_B_NAME = "2026 东南亚供应商走访"

TZ_SHANGHAI = ZoneInfo("Asia/Shanghai")
TZ_EUROPE = ZoneInfo("Europe/Berlin")

FIXTURE_PATH = ROOT / "docs" / "acceptance-tests" / "fixtures" / "sample-customers.xlsx"


# ------------------------------------------------------------------- 身份（owner）
def _ensure_org(session: Session, org_id: UUID, *, name: str, slug: str, tz: str, currency: str) -> Organization:
    org = session.get(Organization, org_id)
    if org is None:
        org = Organization(
            id=org_id,
            name=name,
            slug=slug,
            default_timezone=tz,
            default_currency=currency,
            daily_suggestion_cap=3,
        )
        session.add(org)
        session.flush()
    return org


def _ensure_user(session: Session, user_id: UUID, *, email: str, display_name: str, tz: str) -> User:
    user = session.get(User, user_id)
    if user is None:
        user = User(id=user_id, email=email, display_name=display_name, home_timezone=tz)
        session.add(user)
        session.flush()
    return user


def _ensure_membership(session: Session, org_id: UUID, user_id: UUID, role: str) -> Membership:
    if role not in ROLES:
        raise ValueError(f"未知角色 {role!r}，允许：{ROLES}")
    membership = session.scalars(
        select(Membership).where(
            Membership.organization_id == org_id, Membership.user_id == user_id
        )
    ).one_or_none()
    if membership is None:
        membership = Membership(organization_id=org_id, user_id=user_id, role=role)
        session.add(membership)
        session.flush()
    return membership


def seed_identity() -> dict[str, int]:
    """建组织 / 用户 / 成员关系。

    身份三表是 ``ENABLE`` 但**不** ``FORCE`` RLS 的，因此这里的 ``owner_session()``
    **不需要**传组织上下文——这正是"一次性身份开通"这条特权路径的体现。
    """
    created = {"organizations": 0, "users": 0, "memberships": 0}
    with owner_session() as session:
        before = {name: _count(session, model) for name, model in _IDENTITY_MODELS.items()}

        _ensure_org(session, ORG_A, name="安纳瑞能源", slug=ORG_A_SLUG, tz="Asia/Shanghai", currency="CNY")
        _ensure_org(session, ORG_B, name="Northwind Trading", slug=ORG_B_SLUG, tz="Asia/Singapore", currency="SGD")

        _ensure_user(session, USER_A, email="kerry@anari.example", display_name="Kerry Chu", tz="Asia/Shanghai")
        _ensure_user(session, USER_A2, email="mia@anari.example", display_name="Mia Zhang", tz="Asia/Shanghai")
        _ensure_user(session, USER_B, email="bella@northwind.example", display_name="Bella Ng", tz="Asia/Singapore")

        _ensure_membership(session, ORG_A, USER_A, "OWNER")
        _ensure_membership(session, ORG_A, USER_A2, "ADMIN")
        _ensure_membership(session, ORG_B, USER_B, "TRAVELER")

        after = {name: _count(session, model) for name, model in _IDENTITY_MODELS.items()}
        for name in created:
            created[name] = after[name] - before[name]
    return created


_IDENTITY_MODELS = {"organizations": Organization, "users": User, "memberships": Membership}


def _count(session: Session, model) -> int:
    return int(session.scalar(select(func.count()).select_from(model)) or 0)


# --------------------------------------------------------------- 业务（org 会话）
def _find_trip(session: Session, org_id: UUID, name: str) -> Trip | None:
    return session.scalars(
        select(Trip).where(Trip.organization_id == org_id, Trip.name == name)
    ).one_or_none()


def _find_visit(session: Session, trip_id: UUID, *, place_id: UUID, title: str | None) -> Visit | None:
    return session.scalars(
        select(Visit).where(
            Visit.trip_id == trip_id, Visit.place_id == place_id, Visit.title == title
        )
    ).one_or_none()


def _resolve_place(session: Session, org_id: UUID, **kwargs) -> Place:
    place, _reused = place_service.resolve_or_create_place(session, org_id, **kwargs)
    return place


def _resolve_customer(session: Session, org_id: UUID, **kwargs) -> Customer:
    customer, _reused = customer_service.resolve_or_create_customer(session, org_id, **kwargs)
    return customer


def seed_org_a() -> dict[str, int]:
    """租户 A 的主演示数据：一个欧洲多国出行 + 4 客户 + 4 拜访。"""
    stats = {"trips": 0, "places": 0, "customers": 0, "visits": 0}
    with org_session(ORG_A) as session:
        # 出发地：上海（显式坐标，避免依赖离线索引的信心值）
        shanghai = _resolve_place(
            session,
            ORG_A,
            name="上海总部",
            city="Shanghai",
            country_code="CN",
            address_line="浦东新区张江高科技园区博云路 2 号",
            timezone="Asia/Shanghai",
            lat=31.2045,
            lon=121.5994,
            is_immovable=True,
        )

        trip = _find_trip(session, ORG_A, TRIP_A_NAME)
        if trip is None:
            trip = trip_service.create_trip(
                session,
                ORG_A,
                TripCreate(
                    name=TRIP_A_NAME,
                    purpose="德国与荷兰重点客户年度商务拜访，含产线考察与框架谈判",
                    origin_place_id=shanghai.id,
                    home_timezone="Asia/Shanghai",
                    currency="EUR",
                    # 窗口起点取当日 00:00（本地时间）：首日就要完成上海 → 柏林的跨洲航段
                    # （约 840 分钟）并落在 10:00–12:00 CET 的会议窗内，从 08:00 起算会迟到。
                    window_start=datetime(2026, 11, 9, 0, 0, tzinfo=TZ_SHANGHAI),
                    window_end=datetime(2026, 11, 15, 20, 0, tzinfo=TZ_SHANGHAI),
                    countries=["DE", "NL"],
                    budget_amount=Decimal("15000.00"),
                ),
                owner_user_id=USER_A,
            )
            stats["trips"] += 1
        else:
            # 兼容早期版本种下的同一行程：窗口起点需要收敛到 00:00，否则首日会议不可达。
            desired_start = datetime(2026, 11, 9, 0, 0, tzinfo=TZ_SHANGHAI)
            if trip.window_start != desired_start:
                trip.window_start = desired_start
                stats["reconciled"] = stats.get("reconciled", 0) + 1

        # --- 拜访点：欧洲四点。坐标优先用显式值，缺省则交给离线城市索引。
        berlin = _resolve_place(
            session,
            ORG_A,
            name="柏林出行集团总部",
            city="Berlin",
            country_code="DE",
            address_line="Alexanderplatz 1, 10178 Berlin",
            region="Berlin",
        )
        munich = _resolve_place(
            session,
            ORG_A,
            name="慕尼黑机器人产线",
            city="Munich",
            country_code="DE",
            address_line="Werner-von-Siemens-Ring 1, 85630 Grasbrunn",
            region="Bayern",
            lat=48.1351,
            lon=11.5820,
        )
        amsterdam = _resolve_place(
            session,
            ORG_A,
            name="阿姆斯特丹货运中心",
            city="Amsterdam",
            country_code="NL",
            address_line="Havenstraat 12, 1013 Amsterdam",
            region="Noord-Holland",
        )

        europe_places = (berlin, munich, amsterdam)

        # --- 客户：external_ref 模拟 CRM 编号，tier 决定拜访优先级。
        customers = [
            _resolve_customer(
                session,
                ORG_A,
                name="柏林出行集团",
                country_code="DE",
                external_ref="DE-1002",
                tier="A",
                industry="城市交通运营商",
                primary_contact_name="Lukas Weber",
                primary_contact_email="lukas@berlin-mobility.example",
                primary_contact_phone="+49 30 1000 2000",
                owner_user_id=USER_A,
            ),
            _resolve_customer(
                session,
                ORG_A,
                name="慕尼黑机器人有限公司",
                country_code="DE",
                external_ref="DE-1001",
                tier="A",
                industry="工业自动化",
                primary_contact_name="Anna Bauer",
                primary_contact_email="anna@munich-robotics.example",
                primary_contact_phone="+49 89 3000 4000",
                owner_user_id=USER_A,
            ),
            _resolve_customer(
                session,
                ORG_A,
                name="阿姆斯特丹货运公司",
                country_code="NL",
                external_ref="NL-2001",
                tier="B",
                industry="港口物流",
                primary_contact_name="Sanne de Vries",
                primary_contact_email="sanne@ams-freight.example",
                primary_contact_phone="+31 20 5000 6000",
                owner_user_id=USER_A2,
            ),
            _resolve_customer(
                session,
                ORG_A,
                name="鹿特丹能源解决方案",
                country_code="NL",
                external_ref="NL-2002",
                tier="B",
                industry="能源工程",
                primary_contact_name="Daan Bakker",
                primary_contact_email="daan@rotterdam-energy.example",
                owner_user_id=USER_A2,
            ),
        ]

        # --- 拜访：时间窗 + 商业价值 + 优先级 + 交通方式，构成规划引擎的输入。
        visit_specs: list[tuple[Customer, Place, str, int, int, Decimal | None, str, datetime | None, datetime | None, bool]] = [
            (
                customers[0],
                berlin,
                "柏林出行集团 · 年度框架谈判",
                120,
                1,
                Decimal("180000.00"),
                "AIR",
                datetime(2026, 11, 9, 10, 0, tzinfo=TZ_EUROPE),
                datetime(2026, 11, 9, 12, 0, tzinfo=TZ_EUROPE),
                False,
            ),
            (
                customers[1],
                munich,
                "慕尼黑机器人 · 产线考察",
                90,
                1,
                Decimal("120000.00"),
                "AIR",
                datetime(2026, 11, 10, 9, 0, tzinfo=TZ_EUROPE),
                datetime(2026, 11, 10, 12, 0, tzinfo=TZ_EUROPE),
                True,
            ),
            (
                customers[2],
                amsterdam,
                "阿姆斯特丹货运 · 港口物流方案",
                75,
                2,
                Decimal("60000.00"),
                "GROUND",
                datetime(2026, 11, 12, 14, 0, tzinfo=TZ_EUROPE),
                datetime(2026, 11, 12, 16, 30, tzinfo=TZ_EUROPE),
                False,
            ),
        ]

        for customer, place, title, minutes, priority, value, mode, start, end, immovable in visit_specs:
            existing = _find_visit(session, trip.id, place_id=place.id, title=title)
            if existing is not None:
                # 兼容早期种子：跨欧亚段若写成 RAIL（约 3200 分钟）则当天/次日都到不了，
                # 规划引擎会判不可行。收敛为 AIR 与"上海 → 欧洲"的现实交通一致。
                if existing.movement_mode != mode:
                    existing.movement_mode = mode
                    stats["reconciled"] = stats.get("reconciled", 0) + 1
                continue
            visit_service.create_visit(
                session,
                ORG_A,
                trip.id,
                VisitCreate(
                    place_id=place.id,
                    customer_id=customer.id,
                    title=title,
                    service_minutes=minutes,
                    time_window_start=start,
                    time_window_end=end,
                    priority=priority,
                    business_value=value,
                    movement_mode=mode,  # type: ignore[arg-type]
                    is_immovable=immovable,
                ),
            )
            stats["visits"] += 1

        # 鹿特丹一行刻意**不留时间窗**：设计文档要求时间窗可单边缺失，
        # 它同时给规划引擎制造一个"必须靠优化而非硬约束排序"的点。
        rotterdam = _resolve_place(
            session,
            ORG_A,
            name="鹿特丹港区服务中心",
            city="Rotterdam",
            country_code="NL",
            address_line="Waalhaven Oostzijde 81, 3087 Rotterdam",
            region="Zuid-Holland",
        )
        title = "鹿特丹能源 · 港区现场走访"
        if _find_visit(session, trip.id, place_id=rotterdam.id, title=title) is None:
            visit_service.create_visit(
                session,
                ORG_A,
                trip.id,
                VisitCreate(
                    place_id=rotterdam.id,
                    customer_id=customers[3].id,
                    title=title,
                    service_minutes=60,
                    priority=3,
                    business_value=Decimal("35000.00"),
                    movement_mode="GROUND",
                ),
            )
            stats["visits"] += 1

        stats["places"] = 1 + len(europe_places) + 1
        stats["customers"] = len(customers)
        stats["trip_id"] = str(trip.id)  # type: ignore[assignment]
    return stats


def seed_org_b() -> dict[str, int]:
    """租户 B 的最小数据集：用于证明 RLS 真的把两个租户隔开（AT-S01）。"""
    stats: dict[str, int] = {"trips": 0, "places": 0, "customers": 0, "visits": 0}
    with org_session(ORG_B) as session:
        home = _resolve_place(
            session,
            ORG_B,
            name="新加坡办公室",
            city="Singapore",
            country_code="SG",
            address_line="1 Raffles Place, Singapore 048616",
            timezone="Asia/Singapore",
        )

        trip = _find_trip(session, ORG_B, TRIP_B_NAME)
        if trip is None:
            trip = trip_service.create_trip(
                session,
                ORG_B,
                TripCreate(
                    name=TRIP_B_NAME,
                    purpose="东南亚供应商走访与产能核查",
                    origin_place_id=home.id,
                    home_timezone="Asia/Singapore",
                    currency="SGD",
                    window_start=datetime(2026, 12, 1, 8, 0, tzinfo=ZoneInfo("Asia/Singapore")),
                    window_end=datetime(2026, 12, 5, 20, 0, tzinfo=ZoneInfo("Asia/Singapore")),
                    countries=["SG", "MY"],
                    budget_amount=Decimal("8000.00"),
                ),
                owner_user_id=USER_B,
            )
            stats["trips"] += 1

        supplier = _resolve_place(
            session,
            ORG_B,
            name="柔佛装配厂",
            city="Johor Bahru",
            country_code="MY",
            address_line="Jalan Perindustrian 5, 81400 Senai",
            region="Johor",
        )
        customer = _resolve_customer(
            session,
            ORG_B,
            name="Johor Precision Sdn Bhd",
            country_code="MY",
            external_ref="MY-9001",
            tier="A",
            industry="精密制造",
            primary_contact_name="Aisha Rahman",
            primary_contact_email="aisha@johor-precision.example",
            owner_user_id=USER_B,
        )

        title = "Johor Precision · 产能核查"
        if _find_visit(session, trip.id, place_id=supplier.id, title=title) is None:
            visit_service.create_visit(
                session,
                ORG_B,
                trip.id,
                VisitCreate(
                    place_id=supplier.id,
                    customer_id=customer.id,
                    title=title,
                    service_minutes=90,
                    priority=1,
                    business_value=Decimal("90000.00"),
                    movement_mode="GROUND",
                ),
            )
            stats["visits"] += 1

        stats["places"] = 2
        stats["customers"] = 1
        stats["trip_id"] = str(trip.id)  # type: ignore[assignment]
    return stats


# ----------------------------------------------------------------- Excel 夹具
#: 表头刻意用**中文别名**（与 ``COLUMN_ALIASES`` 对照），并混入一列"备注"制造
#: ``UNKNOWN_COLUMNS`` 警告——真实客户名单里几乎总有这种列，导入器必须能容忍并提示。
FIXTURE_HEADERS = [
    "客户名称",
    "拜访主题",
    "客户编号",
    "客户等级",
    "国家代码",
    "城市",
    "详细地址",
    "纬度",
    "经度",
    "服务时长",
    "优先级",
    "商业价值",
    "时间窗开始",
    "时间窗结束",
    "联系人",
    "邮箱",
    "备注",
]

#: 每行都对应一个预期被识别的问题码，见行尾注释。整表用于验证净空报告的**分类能力**。
#:
#: 会议日期**相对"今天"生成**（+2/+3/+4 天，见 ``_fixture_day``）：夹具写死日期会过期，
#: 导入后的拜访落在过去 → 规划器判不可行，链式/导入用例在规划阶段必挂且难排查。
#: 固定的是"相对关系"（第 5 行与第 1 行同窗去重、第 8 行结束早于开始），日期本身随天滚动。
def _fixture_day(offset: int) -> str:
    """夹具基准日：今天 + offset 天，返回 YYYY-MM-DD。"""
    return (date.today() + timedelta(days=offset)).isoformat()


def _fixture_window(offset: int, start_hhmm: str, end_hhmm: str) -> tuple[str, str]:
    day = _fixture_day(offset)
    return f"{day} {start_hhmm}", f"{day} {end_hhmm}"


def build_fixture_rows() -> list[list[object]]:
    """生成夹具数据行（日期动态，其余字段固定）。"""
    d1_start, d1_end = _fixture_window(2, "09:00", "12:00")
    d2_start, d2_end = _fixture_window(3, "14:00", "16:00")
    # 第 8 行刻意倒置（16:00 → 14:00），验证 INVALID_TIME_WINDOW。
    d3_start, d3_end = _fixture_window(4, "16:00", "14:00")
    return [
    # 1) 正常行：显式坐标，全字段齐备 → VALID
    [
        "慕尼黑机器人有限公司", "产线考察", "DE-1001", "A", "DE", "Munich",
        "Werner-von-Siemens-Ring 1", 48.1351, 11.5820, 90, 1, 120000,
        d1_start, d1_end, "Anna Bauer",
        "anna@munich-robotics.example", "重点客户",
    ],
    # 2) 无坐标但城市在离线索引中 → VALID（坐标由索引补齐，可能带 GEOCODE_LOW_CONFIDENCE）
    [
        "柏林出行集团", "年度框架谈判", "DE-1002", "A", "DE", "Berlin",
        "Alexanderplatz 1", None, None, 120, 1, 180000,
        None, None, "Lukas Weber", "lukas@berlin-mobility.example", None,
    ],
    # 3) 同上，另一国家 → 验证索引按 (城市, 国家) 双键命中
    [
        "阿姆斯特丹货运公司", "港口物流方案", "NL-2001", "B", "NL", "Amsterdam",
        "Havenstraat 12", None, None, 75, 2, 60000,
        None, None, "Sanne de Vries", "sanne@ams-freight.example", None,
    ],
    # 4) 只填纬度 → COORDINATES_PARTIAL（WARNING，坐标回退到城市索引）
    [
        "赫尔辛基试点公司", "北欧试点启动", "FI-3001", "C", "FI", "Helsinki",
        None, 60.17, None, 60, 3, 20000,
        None, None, "Mikko Laine", "mikko@helsinki-pilot.example", "只有纬度",
    ],
    # 5) 与第 1 行同客户同时间 → DUPLICATE_ROW（不静默丢弃，交用户勾选）
    [
        "慕尼黑机器人有限公司", "产线考察", "DE-1001", "A", "DE", "Munich",
        "Werner-von-Siemens-Ring 1", 48.1351, 11.5820, 90, 1, 120000,
        d1_start, d1_end, "Anna Bauer",
        "anna@munich-robotics.example", "重点客户（重复填写）",
    ],
    # 6) 客户名为空 → MISSING_CUSTOMER_NAME（ERROR，整行不可接受）
    [
        None, "临时拜访", "DE-9999", "B", "DE", "Berlin",
        None, None, None, 45, 2, 5000,
        None, None, None, None, "忘了填客户名",
    ],
    # 7) 停留时长写成"2 小时" → INVALID_SERVICE_MINUTES（WARNING，回退默认 60 分钟）
    [
        "布鲁塞尔能源署", "政策沟通", "BE-4001", "B", "BE", "Brussels",
        None, None, None, "2 小时", 2, 30000,
        d2_start, d2_end, "Claire Dubois",
        "claire@be-energy.example", None,
    ],
    # 8) 结束早于开始 → INVALID_TIME_WINDOW（WARNING，结束时间被丢弃）
    [
        "巴黎物流集团", "仓储方案", "FR-5001", "B", "FR", "Paris",
        None, None, None, 60, 2, 45000,
        d3_start, d3_end, "Émile Roux",
        "emile@paris-logistics.example", None,
    ],
    # 9) 国家代码三字母 + 城市不在索引 → INVALID_COUNTRY_CODE + COORDINATES_MISSING
    [
        "亚特兰蒂斯科技", "未知城市测试", "XX-0001", "C", "DEU", "Atlantis",
        None, None, None, 30, 3, 1000,
        None, None, None, None, "故意测试解析失败路径",
    ],
    # 10) 邮箱格式可疑 → INVALID_EMAIL（WARNING）
    [
        "都柏林数据服务", "数据合规沟通", "IE-6001", "C", "IE", "Dublin",
        None, None, None, 45, 3, 15000,
        None, None, "Niamh O'Brien", "niamh.at.dublin-data.example", None,
    ],
]


def write_fixture(path: Path = FIXTURE_PATH) -> Path:
    """生成 Excel 导入夹具。每次覆盖写入，保证内容与上面的常量一致。"""
    from openpyxl import Workbook

    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "客户拜访名单"
    sheet.append(FIXTURE_HEADERS)
    for row in build_fixture_rows():
        sheet.append(row)
    workbook.save(path)
    workbook.close()
    return path


# --------------------------------------------------------------------- 校验输出
def verify() -> None:
    """打印两个租户各自可见的行数——用 A 的会话去看 B 的数据必须是 0（RLS 生效）。"""
    print("\n[verify] 各租户在自身组织上下文下可见的行数")
    for org_id, label in ((ORG_A, "ORG_A"), (ORG_B, "ORG_B")):
        with org_session(org_id) as session:
            trips = _count(session, Trip)
            places = _count(session, Place)
            customers = _count(session, Customer)
            visits = _count(session, Visit)
            batches = _count(session, ImportBatch)
        print(
            f"  {label} ({org_id}) → trips={trips} places={places} "
            f"customers={customers} visits={visits} import_batches={batches}"
        )

    # 跨租户穿透尝试：A 的上下文按 B 的 trip 名查，必须查不到。
    with org_session(ORG_A) as session:
        leaked = session.scalars(
            select(Trip).where(Trip.name == TRIP_B_NAME)
        ).all()
    print(f"  [AT-S01] ORG_A 上下文中检索 ORG_B 的出行 → {len(leaked)} 行（期望 0）")
    if leaked:
        raise SystemExit("RLS 未生效：跨租户数据可见，请先检查 0001/0002 的 RLS 迁移")


def main() -> int:
    parser = argparse.ArgumentParser(description="造开发种子数据（幂等，可重复运行）")
    parser.add_argument("--no-fixture", action="store_true", help="不生成 Excel 导入夹具")
    parser.add_argument("--verify", action="store_true", help="造完后打印可见行数并做跨租户穿透检查")
    parser.add_argument("--fixture-only", action="store_true", help="只生成 Excel 夹具，不碰数据库")
    args = parser.parse_args()

    if args.fixture_only:
        path = write_fixture()
        print(f"[ok] Excel 夹具已写入 {path}（{len(build_fixture_rows())} 行数据 + 1 行表头）")
        return 0

    identity = seed_identity()
    print(
        f"[ok] 身份：新增 organizations={identity['organizations']} "
        f"users={identity['users']} memberships={identity['memberships']}（已存在的不重复创建）"
    )

    stats_a = seed_org_a()
    print(
        f"[ok] ORG_A：新增 trips={stats_a['trips']} visits={stats_a['visits']}"
        f"（现有 places={stats_a['places']} customers={stats_a['customers']}，按自然键复用）"
    )

    stats_b = seed_org_b()
    print(f"[ok] ORG_B：新增 trips={stats_b['trips']} visits={stats_b['visits']}")

    if not args.no_fixture:
        path = write_fixture()
        print(f"[ok] Excel 夹具已写入 {path}")

    print(
        "\n[i] 下一步：用 GBT_DEV_ORG_ID=11111111-1111-1111-1111-111111111111 "
        "启动 services/api（uvicorn app.main:app），再在 iOS 客户端或 curl 上验证。"
    )

    if args.verify:
        verify()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())