"""日历写入加固 smoke(fatal-validation 契约):save_calendar/append_calendar 对非法日期串
直接抛 CalendarValidationError + 原子写(tmp→os.replace),防 '129188-42-' 这类脏行污染 day.txt。
**全程临时目录,绝不碰 G:/stocks。** 用 venv python 跑 → 默认 import 解析到 G:/financial-analyst/src
(即 `fa data update` 实际用的那份)。"""
import shutil
import tempfile
from pathlib import Path

from financial_analyst.data.bin_writer import (  # noqa: E402
    append_calendar, save_calendar, load_calendar, CalendarValidationError)

GARBAGE = ["129188-42-", "1336-94-31", "799-55-59", "89172-58-2", "2026-13-40"]  # 末个 regex 过但月份非法
VALID = ["2025-01-06", "2025-01-07", "2025-01-08"]  # 周一/二/三


def _raises(fn, *a):
    try:
        fn(*a)
    except CalendarValidationError:
        return True
    return False


print("bin_writer path:", __import__("financial_analyst.data.bin_writer",
      fromlist=["__file__"]).__file__)
tmp = Path(tempfile.mkdtemp(prefix="calguard_"))
try:
    prov = str(tmp)
    # 1) 合法日期正常写入
    assert append_calendar(VALID, prov, "day") == 3, "合法写入计数错"
    assert load_calendar(prov, "day") == sorted(VALID), "合法日历内容错"
    # 2) 每个垃圾串 append 都抛 CalendarValidationError(致命,不静默吞)
    for g in GARBAGE:
        assert _raises(append_calendar, [g], prov, "day"), "垃圾未被拒:" + g
    # 3) save_calendar 直接喂含垃圾的列表 → 抛
    assert _raises(save_calendar, ["2025-01-02", "129188-42-"], prov, "day"), "save 未拒垃圾"
    # 4) 垃圾 append 失败后,原日历未被污染
    assert load_calendar(prov, "day") == sorted(VALID), "垃圾 append 失败后日历被改了"
    # 5) 原子写:不留 .tmp 残file
    leftover = list((tmp / "calendars").glob("*.tmp*"))
    assert not leftover, ("残留 tmp 文件", leftover)
    # 6) 5min/datetime 合法条目可过(intraday 日历)
    save_calendar(["2025-01-06 09:35:00", "2025-01-06 09:40:00"], prov, "5min")
    assert len(load_calendar(prov, "5min")) == 2, "5min 日历写入错"
    print("calendar-guard smoke PASS · day =", load_calendar(prov, "day"))
finally:
    shutil.rmtree(tmp, ignore_errors=True)
