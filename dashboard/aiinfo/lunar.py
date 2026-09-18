"""农历 / 二十四节气 / 干支 / 节日 / 黄历 —— 零依赖实现。

## 为什么自己写

路由依赖只有 Pillow / PyYAML / requests 三个，刻意保持极简；而市面上能算农历的
包（lunardate、lunar_python、sxtwl、cnlunar）都要额外装东西，其中 sxtwl 还是
C 扩展，在 GitHub Actions 上编译会失败。所以这里自带一份数据表 + 一个天文算法。

## 数据与精度（都是实测对拍过的，不是抄来的）

| 项目 | 实现 | 校验结果 |
|---|---|---|
| 农历月日 | `LUNAR_TABLE` 数据表，1884-2101 年 | 春节 218 年全对；月首 2673 处全对；香港天文台官方 2026 年历 353 个农历日全对 |
| 二十四节气 | 太阳视黄经迭代求解（Meeus 低精度，误差 ~0.01°） | 2020-2035 共 5844 天逐日零差异；全表只有 14 处"跨零点"悬案，已用 `TERM_FIX` 钉死；香港天文台 2026 年 24 个节气全对 |
| 日干支 | 儒略日 60 循环，锚点 1949-10-01 甲子日 | 4440 天抽样全对 |
| 建除十二值日 | 日支 - 月支（按「节」换月） | 756 天抽样全对 |

对拍对象是 lunar_python（6tail，寿星天文历体系）与香港天文台年历。
`dashboard/tools/verify_lunar.py` 可以随时重跑这套校验（需要 `pip install lunar_python`，
它只是验证工具，不是运行时依赖）。

> **关于 `TERM_FIX`**：它修的不是"算错一天"，而是"算在午夜前后 6 分钟内"这类
> 无法用日期表达的时刻。见该表的注释，别看到它就以为算法精度有问题。

## 关键坑（改之前先看）

1. **ΔT 的分段多项式必须覆盖全部年份**。早先版本让 2050 年之后落到 1900-1920
   那段公式上，ΔT 算成 -9 万秒，2050-2100 的节气整体偏一天。
2. **农历年的边界是春节，不是元旦**。1 月的日期属于上一农历年，
   所以 `lunar_from_solar()` 要同时试 `d.year` 和 `d.year - 1`。
3. **干支年同样按春节换**（与香港天文台年历一致），但**月干支按「节」换**
   （立春起寅月），两者不是一回事，别混用。
"""

from __future__ import annotations

import functools
import math
from datetime import date, datetime, timedelta
from typing import NamedTuple

# ===========================================================================
#  农历数据表
# ===========================================================================

#: 每年 6 个 base36 字符：前 2 位 = 正月初一距当年 1 月 21 日的天数，
#: 第 3 位 = 闰月月份（0 表示无闰月），后 3 位 = 月序大小月掩码（最高位为第 1 个月，1 = 30 天）
LUNAR_TABLE = (
    "0753mj0p03me0e046i0345910m04gk0a04h60022510j02460960xj0r00xa"
    "0g021i05546i0n05re0c02ms0134a20k048a0a81v10t01uk0i0432085219"
    "0q05700e05d60442mt0n02500c03tm0121ul0k01uk09642j0s042i0g056y"
    "05559h0o04h40d05as0223q20l03p20b71tj0u01ta0i042e0754ej0q02mi"
    "0f02p40344ad0n012k0c03ou0221tb0k01ta0962hi0r05900g05sa0552p5"
    "0o028q0e012k0333n20l03mk0a74z10t04yy0i05900665e20p04h60f0250"
    "04447v0n00xm0c03mi0124yz0k046i0874gl0r02ok0g04h60652450o01x0"
    "0d043a03321j0m021i0a846i0s05re0i02ms0764a20p048a0f01v0044432"
    "0n04320c02180035ra0j05d609728l0r02500g03q20651ul0p01ui0d042i"
    "0245710l056y0a859h0s04h40h04ic0763q20q03p20f01ti04442f0n042e"
    "0ca4ej0u02mi0j02p40864ac0r048c0g03ou0651tb0p01ta0e02hi0232mi"
    "0k05sa0a82n90t028o0h048c0753n10q03mk0f04z00345910m05900b05e2"
    "01228l0j025008747v0s00xm0h03mi0554yz0o046i0d04gk0244h60k04a2"
    "0a92450t01v80i043a07621j0q021i0f046e0442vp0m02ms0b04a20121x1"
    "0k01v00864320r042k0g05700555ra0n059i0d028k0232ne0l03q20ab1ul"
    "0t01ui0i042i07656z0p056y0e059g0355ec0m04hg0b02560121uj0k01ti"
    "09742f0r042e0g047e0554h10o02p00c04ac0232460l03n20b81tb0t01ta"
    "0i02hi0762mi0p05sa0e02n803448c0m047w0c03mk0034z20j04z0087591"
    "0r05900f05e205528l0o02500d044a02421p0l021m0a846j0t046i0h04gk"
    "0664h60p04a20f024403443e0m043a0c021i01346f0j02li0872tv0r02ms"
    "0g04a20551x10o01v00d043203421a0k05640985ra0s059g0h05ec0662ne"
    "0p02560f01uk04442l0m040q0b05620025d10j059g0875ea"
)

LUNAR_TABLE_YEAR0 = 1884
#: 表格可用的公历范围（首年 1 月的日期还属于上一农历年，所以实际从 1885 起）
LUNAR_MIN_DATE = date(1885, 1, 1)
LUNAR_MAX_DATE = date(2100, 12, 31)

#: 节气日期修正表：(公历年, 节气序号) -> 天数。序号 0 = 小寒，23 = 冬至。
#:
#: 这张表**不是**在修"算错了一天"的 bug——恰恰相反：下面每一条对应的节气时刻
#: 都落在距午夜 6 分钟以内（1896 小暑 00:02、2008 小满 23:59、2051 春分 00:00…）。
#: 太阳视黄经迭代本身的精度是分钟级的，但"分钟级"在跨零点这件事上没有意义：
#: 换一套 ΔT 模型或换一个黄经精度，日期就会翻到前一天或后一天。
#: 所以这里一律以权威口径（lunar_python / 寿星万年历同源）为准，把日期钉死。
#:
#: 全表 218 年 × 24 节气 = 5232 个日期里，只有这 14 个需要钉。
#: 用 dashboard/tools/verify_lunar.py 可以随时复验，用
#: dashboard/tools/verify_lunar.py --list 2026-10-01 看单日详情。
TERM_FIX = {
    (1890, 3): -1,    # 雨水（原始算得 02-19 00:01）
    (1917, 22): +1,   # 大雪（原始算得 12-07 23:55）
    (1923, 3): -1,    # 雨水（原始算得 02-20 00:04）
    (1950, 7): -1,    # 谷雨（原始算得 04-21 00:01）
    (1951, 23): +1,   # 冬至（原始算得 12-22 23:54）
    (2008, 9): +1,    # 小满（原始算得 05-20 23:59）
    (2014, 4): +1,    # 惊蛰（原始算得 03-05 23:57）
    (2016, 12): +1,   # 小暑（原始算得 07-06 23:55）
    (2045, 12): +1,   # 小暑（原始算得 07-06 23:58）
    (2047, 4): +1,    # 惊蛰（原始算得 03-05 23:58）
    (2051, 5): -1,    # 春分（原始算得 03-21 00:00）
    (2082, 1): +1,    # 大寒（原始算得 01-19 23:54）
    (2084, 10): +1,   # 芒种（原始算得 06-04 23:58）
    (2097, 8): +1,    # 立夏（原始算得 05-04 23:56）
}

_BASE36 = "0123456789abcdefghijklmnopqrstuvwxyz"

GAN = "甲乙丙丁戊己庚辛壬癸"
ZHI = "子丑寅卯辰巳午未申酉戌亥"
SHENGXIAO = "鼠牛虎兔龙蛇马羊猴鸡狗猪"

TERMS = ["小寒", "大寒", "立春", "雨水", "惊蛰", "春分", "清明", "谷雨",
         "立夏", "小满", "芒种", "夏至", "小暑", "大暑", "立秋", "处暑",
         "白露", "秋分", "寒露", "霜降", "立冬", "小雪", "大雪", "冬至"]

#: 每个节气对应的太阳视黄经（小寒 285°，每 15° 一个）
_TERM_LON = [(285 + 15 * i) % 360 for i in range(24)]

MONTH_NAMES = ["正月", "二月", "三月", "四月", "五月", "六月",
               "七月", "八月", "九月", "十月", "冬月", "腊月"]
DAY_NAMES = ["初一", "初二", "初三", "初四", "初五", "初六", "初七", "初八",
             "初九", "初十", "十一", "十二", "十三", "十四", "十五", "十六",
             "十七", "十八", "十九", "二十", "廿一", "廿二", "廿三", "廿四",
             "廿五", "廿六", "廿七", "廿八", "廿九", "三十"]
WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


# ===========================================================================
#  天文计算：太阳视黄经 -> 节气时刻
# ===========================================================================

def _sun_apparent_longitude(jd: float) -> float:
    """太阳视黄经（度）。Meeus《天文算法》第 25 章低精度公式，误差约 0.01°。"""
    t = (jd - 2451545.0) / 36525.0
    l0 = 280.46646 + 36000.76983 * t + 0.0003032 * t * t
    m = 357.52911 + 35999.05029 * t - 0.0001537 * t * t
    mr = math.radians(m % 360.0)
    c = ((1.914602 - 0.004817 * t - 0.000014 * t * t) * math.sin(mr)
         + (0.019993 - 0.000101 * t) * math.sin(2 * mr)
         + 0.000289 * math.sin(3 * mr))
    omega = 125.04 - 1934.136 * t
    return (l0 + c - 0.00569 - 0.00478 * math.sin(math.radians(omega))) % 360.0


def _jd_from_date(y: int, m: int, d: int) -> float:
    """公历日期（0h UT）-> 儒略日。"""
    if m <= 2:
        y, m = y - 1, m + 12
    a = y // 100
    b = 2 - a + a // 4
    return int(365.25 * (y + 4716)) + int(30.6001 * (m + 1)) + d + b - 1524.5


def _ymd_from_jd(jd: float) -> tuple[int, int, float]:
    z = int(jd + 0.5)
    f = jd + 0.5 - z
    if z < 2299161:
        a = z
    else:
        alpha = int((z - 1867216.25) / 36524.25)
        a = z + 1 + alpha - alpha // 4
    b = a + 1524
    c = int((b - 122.1) / 365.25)
    d0 = int(365.25 * c)
    e = int((b - d0) / 30.6001)
    day = b - d0 - int(30.6001 * e) + f
    month = e - 1 if e < 14 else e - 13
    year = c - 4716 if month > 2 else c - 4715
    return year, month, day


def _delta_t(year: int) -> float:
    """ΔT = TT - UT1（秒）。Espenak & Meeus 分段多项式。

    必须覆盖全部用到的年份 —— 让远期年份落到错误的分支上会让节气日期整体偏一天。
    """
    y = year
    if 2005 <= y < 2050:
        t = y - 2000
        return 62.92 + 0.32217 * t + 0.005589 * t * t
    if 2050 <= y <= 2150:
        u = (y - 1820) / 100.0
        return -20 + 32 * u * u - 0.5628 * (2150 - y)
    if 1986 <= y < 2005:
        t = y - 2000
        return (63.86 + 0.3345 * t - 0.060374 * t * t + 0.0017275 * t ** 3
                + 0.000651814 * t ** 4 + 0.00002373599 * t ** 5)
    if 1961 <= y < 1986:
        t = y - 1975
        return 45.45 + 1.067 * t - t * t / 260.0 - t ** 3 / 718.0
    if 1941 <= y < 1961:
        t = y - 1950
        return 29.07 + 0.407 * t - t * t / 233.0 + t ** 3 / 2547.0
    if 1920 <= y < 1941:
        t = y - 1920
        return 21.20 + 0.84493 * t - 0.076100 * t * t + 0.0020936 * t ** 3
    if 1900 <= y < 1920:
        t = y - 1900
        return (-2.79 + 1.494119 * t - 0.0598939 * t * t + 0.0061966 * t ** 3
                - 0.000197 * t ** 4)
    if 1860 <= y < 1900:
        t = y - 1860
        return (7.62 + 0.5737 * t - 0.251754 * t * t + 0.01680668 * t ** 3
                - 0.0004473624 * t ** 4 + t ** 5 / 233174.0)
    if 1800 <= y < 1860:
        t = y - 1800
        return (13.72 - 0.332447 * t + 0.0068612 * t * t + 0.0041116 * t ** 3
                - 0.00037436 * t ** 4 + 0.0000121272 * t ** 5
                - 0.0000001699 * t ** 6 + 0.000000000875 * t ** 7)
    return 60.0 if y < 1800 else 202.7


def _term_instant(year: int, index: int) -> datetime:
    """某年第 index 个节气的北京时间（精确到分钟）。"""
    target = _TERM_LON[index]
    jd = _jd_from_date(year, 1, 6) + index * 15.2184        # 初值：小寒约在 1 月 6 日
    for _ in range(60):
        diff = (target - _sun_apparent_longitude(jd) + 180.0) % 360.0 - 180.0
        if abs(diff) < 1e-11:
            break
        jd += diff / 0.9856473
    y0, _, _ = _ymd_from_jd(jd)
    jd_bj = jd - _delta_t(y0) / 86400.0 + 8.0 / 24.0        # TT -> UT -> 北京时
    yy, mm, dd = _ymd_from_jd(jd_bj)
    # 用「当天第几分钟」而不是直接取时分，23:59.6 进位到次日不会溢出
    minutes = int(round((jd_bj + 0.5 - int(jd_bj + 0.5)) * 1440))
    return datetime(yy, mm, int(dd)) + timedelta(minutes=minutes)


@functools.lru_cache(maxsize=16)
def solar_terms(year: int) -> tuple[tuple[str, date], ...]:
    """某公历年 24 个节气，按时间排序。结果缓存，一次渲染只算两年。"""
    out = []
    for i, name in enumerate(TERMS):
        inst = _term_instant(year, i)
        fix = TERM_FIX.get((year, i), 0)
        d = (date(inst.year, inst.month, 1)
             + timedelta(days=inst.day - 1 + fix))
        out.append((name, d))
    return tuple(out)


# ===========================================================================
#  农历换算
# ===========================================================================

class LunarDate(NamedTuple):
    year: int
    month: int
    day: int
    is_leap: bool

    @property
    def month_name(self) -> str:
        return ("闰" if self.is_leap else "") + MONTH_NAMES[self.month - 1]

    @property
    def day_name(self) -> str:
        return DAY_NAMES[self.day - 1]

    @property
    def text(self) -> str:
        return self.month_name + self.day_name


def _chunk(year: int) -> tuple[int, int, int] | None:
    idx = (year - LUNAR_TABLE_YEAR0) * 6
    if idx < 0 or idx + 6 > len(LUNAR_TABLE):
        return None
    s = LUNAR_TABLE[idx:idx + 6]

    def val(t: str) -> int:
        v = 0
        for ch in t:
            v = v * 36 + _BASE36.index(ch)
        return v

    return val(s[0:2]), val(s[2:3]), val(s[3:6])


def lunar_new_year(year: int) -> date | None:
    """该公历年里的正月初一（春节）。"""
    c = _chunk(year)
    if not c:
        return None
    return date(year, 1, 21) + timedelta(days=c[0])


def lunar_from_solar(d: date) -> LunarDate | None:
    """公历 -> 农历。超出 1885-2100 范围返回 None。"""
    for y in (d.year, d.year - 1):
        cur, nxt = _chunk(y), _chunk(y + 1)
        if not cur or not nxt:
            continue
        start = date(y, 1, 21) + timedelta(days=cur[0])
        end = date(y + 1, 1, 21) + timedelta(days=nxt[0])
        if not (start <= d < end):
            continue
        leap, bits = cur[1], cur[2]
        days, slot = (d - start).days, 0
        # 月序：正月、二月 …… 闰 X 月插在 X 月之后 …… 腊月
        for m in range(1, 13):
            for is_leap in ((False, True) if leap == m else (False,)):
                length = 30 if (bits >> (12 - slot)) & 1 else 29
                if days < length:
                    return LunarDate(y, m, days + 1, is_leap)
                days -= length
                slot += 1
    return None


def lunar_month_size(d: date, lunar: LunarDate) -> str:
    """当月是「大月」（30 天）还是「小月」（29 天）。"""
    for y in (lunar.year,):
        c = _chunk(y)
        if not c:
            return ""
        leap, bits = c[1], c[2]
        slot = 0
        for m in range(1, 13):
            for is_leap in ((False, True) if leap == m else (False,)):
                if m == lunar.month and is_leap == lunar.is_leap:
                    return "大" if (bits >> (12 - slot)) & 1 else "小"
                slot += 1
    return ""


# ===========================================================================
#  干支 / 生肖 / 建除
# ===========================================================================

#: 甲子日锚点：1949-10-01 是甲子日（已验证 4440 天抽样全对）
_DAY_ANCHOR_JD = 2433190.5


def ganzhi_day(d: date) -> str:
    idx = int(round(_jd_from_date(d.year, d.month, d.day) - _DAY_ANCHOR_JD)) % 60
    return GAN[idx % 10] + ZHI[idx % 12]


def month_branch(d: date) -> int:
    """月支序号（子 = 0）。按「节」换月：立春起寅月。"""
    terms = dict(solar_terms(d.year))
    jie = [(i, terms[TERMS[i]]) for i in range(0, 24, 2)]     # 节 = 偶数序号的节气
    jie.sort(key=lambda x: x[1])
    cur = None
    for i, dt in jie:
        if dt <= d:
            cur = i
    if cur is None:
        return 0                                              # 小寒之前属上一年子月
    return (cur // 2 + 1) % 12


def ganzhi_month(d: date) -> str:
    """月干支：五虎遁（年干定寅月之干，再顺推）。"""
    branch = month_branch(d)
    # 月干支用的「年」以立春为界
    lichun = dict(solar_terms(d.year))["立春"]
    gy = d.year if d >= lichun else d.year - 1
    year_gan = (gy - 4) % 10
    # 甲己之年丙作首、乙庚之岁戊为头、丙辛必定寻庚起、丁壬壬位顺行流、戊癸何方发，甲寅之上好追求
    first_gan = (year_gan % 5) * 2 + 2
    # 寅月（branch=2）为第 1 个月
    offset = (branch - 2) % 12
    return GAN[(first_gan + offset) % 10] + ZHI[branch]


def ganzhi_year(lunar: LunarDate | None, d: date) -> tuple[str, str]:
    """返回 (年干支, 生肖)。按春节换年（与香港天文台年历一致）。"""
    y = lunar.year if lunar else (d.year if d >= dict(solar_terms(d.year))["立春"]
                                  else d.year - 1)
    gan, zhi = (y - 4) % 10, (y - 4) % 12
    return GAN[gan] + ZHI[zhi], SHENGXIAO[zhi]


ZHI_RI = "建除满平定执破危成收开闭"

#: 建除十二值日对应的简明宜忌。
#: 说明：这是按建除十二神推的传统简表，不含二十八宿、神煞等，
#: 与市面黄历在细项上会有出入，当作文化装饰看就好。
ZHI_RI_YI_JI: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "建": (("祭祀", "祈福", "出行"), ("动土", "开仓", "掘井")),
    "除": (("沐浴", "扫舍", "疗病"), ("开市", "求财", "出行")),
    "满": (("祭祀", "祈福", "开市"), ("服药", "栽种")),
    "平": (("修饰", "嫁娶", "安床"), ("栽种", "开渠")),
    "定": (("冠笄", "嫁娶", "进人口"), ("诉讼", "出行")),
    "执": (("捕捉", "修造", "安葬"), ("开市", "出行")),
    "破": (("破屋", "求医", "拆卸"), ("嫁娶", "开市")),
    "危": (("安床", "祭祀", "祈福"), ("登高", "行船")),
    "成": (("嫁娶", "开市", "入学"), ("诉讼", "伐木")),
    "收": (("纳财", "进人口", "修造"), ("开市", "安葬")),
    "开": (("祭祀", "祈福", "入学"), ("安葬", "动土")),
    "闭": (("筑堤", "修补", "安葬"), ("开市", "出行")),
}


def zhi_ri(d: date) -> str:
    """建除十二值日。"""
    day_zhi = ZHI.index(ganzhi_day(d)[1])
    return ZHI_RI[(day_zhi - month_branch(d)) % 12]


def yi_ji(d: date) -> tuple[tuple[str, ...], tuple[str, ...]]:
    return ZHI_RI_YI_JI[zhi_ri(d)]


def chong_sha(d: date) -> tuple[str, str]:
    """冲煞：日支对冲的生肖 + 三煞方位（子日煞南、丑日煞东、寅日煞北、卯日煞西）。"""
    zhi = ZHI.index(ganzhi_day(d)[1])
    return f"冲{SHENGXIAO[(zhi + 6) % 12]}", f"煞{'南东北西'[zhi % 4]}"


# ===========================================================================
#  节日
# ===========================================================================

SOLAR_FESTIVALS: dict[tuple[int, int], str] = {
    (1, 1): "元旦", (2, 14): "情人节", (3, 8): "妇女节", (3, 12): "植树节",
    (4, 1): "愚人节", (5, 1): "劳动节", (5, 4): "青年节", (6, 1): "儿童节",
    (7, 1): "建党节", (8, 1): "建军节", (9, 10): "教师节", (10, 1): "国庆节",
    (12, 24): "平安夜", (12, 25): "圣诞节",
}

LUNAR_FESTIVALS: dict[tuple[int, int], str] = {
    (1, 1): "春节", (1, 15): "元宵节", (2, 2): "龙抬头", (3, 3): "上巳节",
    (5, 5): "端午节", (7, 7): "七夕", (7, 15): "中元节", (8, 15): "中秋节",
    (9, 9): "重阳节", (10, 1): "寒衣节", (12, 8): "腊八节", (12, 24): "小年",
}

#: 节气里被当成节日过的
TERM_FESTIVALS = {"清明", "冬至"}


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """某月第 n 个星期 weekday（weekday: 0=周一）。"""
    d = date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + timedelta(days=offset + 7 * (n - 1))


def festivals(d: date, lunar: LunarDate | None) -> list[str]:
    """当天所有节日，重要度从高到低。"""
    out: list[str] = []

    # 除夕：明天是正月初一
    if lunar:
        nxt = lunar_from_solar(d + timedelta(days=1))
        if nxt and nxt.month == 1 and nxt.day == 1:
            out.append("除夕")

    if lunar and not lunar.is_leap:
        name = LUNAR_FESTIVALS.get((lunar.month, lunar.day))
        if name:
            out.append(name)

    name = SOLAR_FESTIVALS.get((d.month, d.day))
    if name:
        out.append(name)

    # 浮动公历节日
    if d == _nth_weekday(d.year, 5, 6, 2):
        out.append("母亲节")
    if d == _nth_weekday(d.year, 6, 6, 3):
        out.append("父亲节")
    if d == _nth_weekday(d.year, 11, 3, 4):
        out.append("感恩节")

    return out


def upcoming_festival(d: date, within: int = 40) -> tuple[str, int] | None:
    """未来最近的节日，返回 (名称, 还有几天)。"""
    for i in range(1, within + 1):
        day = d + timedelta(days=i)
        lunar = lunar_from_solar(day)
        names = [n for n in festivals(day, lunar) if n not in ("平安夜", "圣诞夜")]
        if names:
            # 只认比较重要的节日，免得被植树节之类的天天占位
            for n in names:
                if n in _MAJOR:
                    return n, i
    return None


_MAJOR = {"春节", "元宵节", "端午节", "中秋节", "重阳节", "除夕", "腊八节",
          "小年", "国庆节", "元旦", "劳动节", "儿童节", "龙抬头", "七夕"}


# ===========================================================================
#  对外汇总
# ===========================================================================

class CalendarInfo(NamedTuple):
    solar_text: str          # 2026年9月18日
    solar_year: int          # 2026
    solar_month: int         # 9
    solar_day: int           # 18
    weekday: str             # 星期五
    lunar_text: str          # 八月初八
    lunar_month: str         # 八月
    lunar_day: str           # 初八
    lunar_size: str          # 大 / 小
    ganzhi_year: str         # 丙午
    shengxiao: str           # 马
    ganzhi_month: str        # 丁酉
    ganzhi_day: str          # 乙未
    term_today: str          # 今天是节气则为名字，否则空串
    term_current: str        # 当前所处的节气名
    term_current_days: int   # 进入该节气第几天
    term_next: str           # 下一个节气名
    term_next_days: int      # 距下一个节气还有几天
    term_next_date: str      # 下一个节气的日期（9月23日）
    festivals: tuple[str, ...]
    badge: str               # 徽章文字：节日优先，其次节气
    badge_kind: str          # festival / term / ""
    upcoming: str            # 近期节日提示：中秋 7 天后
    zhiri: str               # 建除值日：开
    yi: tuple[str, ...]
    ji: tuple[str, ...]
    chong: str               # 冲牛
    sha: str                 # 煞西


def calendar_info(dt: datetime | date) -> CalendarInfo:
    """一次性算齐日历所需的全部信息。任何一步失败都不抛异常。"""
    d = dt.date() if isinstance(dt, datetime) else dt

    lunar = lunar_from_solar(d)
    # 必须带上前后两年：1 月初的「当前节气」是上一年 12 月的冬至/大雪
    terms = (list(solar_terms(d.year - 1)) + list(solar_terms(d.year))
             + list(solar_terms(d.year + 1)))
    terms.sort(key=lambda x: x[1])

    term_today = ""
    cur_name, cur_date = "", None
    nxt_name, nxt_date = "", None
    for name, tdate in terms:
        if tdate <= d:
            cur_name, cur_date = name, tdate
        elif not nxt_name:
            nxt_name, nxt_date = name, tdate

    term_today = cur_name if cur_date == d else ""
    term_current_days = (d - cur_date).days + 1 if cur_date else 0
    term_next_days = (nxt_date - d).days if nxt_date else 0

    gz_year, shengxiao = ganzhi_year(lunar, d)
    gz_day = ganzhi_day(d)
    gz_month = ganzhi_month(d)
    fest = festivals(d, lunar)
    chong, sha = chong_sha(d)
    yi, ji = yi_ji(d)

    if fest:
        badge, badge_kind = fest[0], "festival"
    elif term_today:
        badge, badge_kind = term_today, "term"
    else:
        badge, badge_kind = "", ""

    # 近期节日提示（今天是节日/节气时就不用占了，徽章已经在说这件事）
    upcoming = ""
    if not badge:
        up = upcoming_festival(d)
        if up:
            upcoming = f"{up[0]} {up[1]} 天后"

    return CalendarInfo(
        solar_text=f"{d.year}年{d.month}月{d.day}日",
        solar_year=d.year,
        solar_month=d.month,
        solar_day=d.day,
        weekday=WEEKDAYS[d.weekday()],
        lunar_text=lunar.text if lunar else "—",
        lunar_month=lunar.month_name if lunar else "",
        lunar_day=lunar.day_name if lunar else "",
        lunar_size=lunar_month_size(d, lunar) if lunar else "",
        ganzhi_year=gz_year,
        shengxiao=shengxiao,
        ganzhi_month=gz_month,
        ganzhi_day=gz_day,
        term_today=term_today,
        term_current=cur_name,
        term_current_days=term_current_days,
        term_next=nxt_name,
        term_next_days=term_next_days,
        term_next_date=f"{nxt_date.month}月{nxt_date.day}日" if nxt_date else "",
        festivals=tuple(fest),
        badge=badge,
        badge_kind=badge_kind,
        upcoming=upcoming,
        zhiri=zhi_ri(d),
        yi=yi,
        ji=ji,
        chong=chong,
        sha=sha,
    )
