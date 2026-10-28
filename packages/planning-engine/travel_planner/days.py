"""按 `timezone_home` 切分出行窗口为"工作日"。纯计算，无 IO。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from datetime import date as date_type
from zoneinfo import ZoneInfo

from travel_planner.contracts import ProblemWindow

#: 防御性上限：出行窗口不应跨年；超过即视为调用方错误，避免无界迭代。
MAX_DAYS = 400

_ONE_SECOND = timedelta(seconds=1)


@dataclass(frozen=True)
class DayWindow:
    """单日的可用时间界（含首尾）。"""

    date: date_type
    open: datetime
    close: datetime

    @property
    def minutes(self) -> int:
        seconds = (self.close - self.open).total_seconds()
        return int(seconds // 60) + 1


def local_zone(name: str) -> ZoneInfo:
    return ZoneInfo(name)


def as_aware(value: datetime, tz: ZoneInfo) -> datetime:
    """朴素时间按 `timezone_home` 解释；已带时区的保持其瞬时值。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=tz)
    return value


def enumerate_days(window: ProblemWindow, timezone_home: str) -> tuple[DayWindow, ...]:
    """把出行窗口按本地时区切成逐日区间（升序、确定性）。

    - 窗口首日从 `window.start` 起，末日到 `window.end` 止；
    - 中间日按本地 00:00:00 → 23:59:59 切分。
    """
    tz = local_zone(timezone_home)
    start = as_aware(window.start, tz)
    end = as_aware(window.end, tz)

    first = start.astimezone(tz).date()
    last = end.astimezone(tz).date()
    if (last - first).days + 1 > MAX_DAYS:
        raise ValueError(f"trip window spans more than {MAX_DAYS} days")

    days: list[DayWindow] = []
    cursor = first
    while cursor <= last:
        midnight = datetime.combine(cursor, time(0, 0, 0), tzinfo=tz)
        day_close = midnight + timedelta(days=1) - _ONE_SECOND
        day_open = start if start > midnight else midnight
        close = end if end < day_close else day_close
        if close >= day_open:
            days.append(DayWindow(date=cursor, open=day_open, close=close))
        cursor = cursor + timedelta(days=1)

    if not days:
        raise ValueError("trip window contains no usable day")
    return tuple(days)


def add_elapsed(moment: datetime, minutes: int) -> datetime:
    """按**真实经过时间**推进时刻，再换回原时区的墙上时间。

    直接对带时区的 datetime 加 timedelta 是墙上时间运算；跨夏令时切换时会把 60 分钟
    算成 0 或 120 分钟。航班/会议时长都是真实经过时间，必须先转到 UTC 再相加。
    """
    if moment.tzinfo is None:
        return moment + timedelta(minutes=minutes)
    return (moment.astimezone(UTC) + timedelta(minutes=minutes)).astimezone(
        moment.tzinfo
    )


def elapsed_minutes(start: datetime, end: datetime) -> int:
    """两个时刻之间真实经过的整分钟数；朴素时间按墙上时间相减。"""
    if start.tzinfo is None or end.tzinfo is None:
        return int((end - start).total_seconds() // 60)
    return int(
        (end.astimezone(UTC) - start.astimezone(UTC)).total_seconds()
        // 60
    )


def minutes_between(base: datetime, moment: datetime) -> int:
    """相对 `base` 的分钟偏移（向上取整），用于把绝对时间映射到求解器的整数时间轴。"""
    delta = (moment - base).total_seconds()
    if delta <= 0:
        return 0
    return int(-(-delta // 60))