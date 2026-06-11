# smart_mart/utils/nepali_date.py
# ================================
# Bikram Sambat (BS) ↔ Gregorian (AD) conversion
# No external library needed — pure Python lookup table.

# BS year data: [total_days_in_year, days_per_month x12]
_BS_YEAR_DATA = {
    2070: [365, 31,31,32,31,31,31,30,29,30,29,30,30],
    2071: [365, 31,31,32,31,31,31,30,29,30,29,30,30],
    2072: [366, 31,32,31,32,31,30,30,30,29,29,30,30],
    2073: [365, 31,32,31,32,31,30,30,30,29,29,30,30],
    2074: [365, 31,32,31,32,31,30,30,29,30,29,30,30],
    2075: [365, 31,31,32,31,31,30,30,29,30,29,30,31],
    2076: [365, 31,31,32,31,31,30,30,29,30,29,30,30],
    2077: [365, 31,32,31,32,31,30,30,30,29,29,30,30],
    2078: [366, 31,31,32,31,31,31,30,29,30,29,30,30],
    2079: [365, 31,31,32,31,31,31,30,29,30,29,30,30],
    2080: [365, 31,32,31,32,31,30,30,30,29,29,30,30],
    2081: [366, 31,32,31,32,31,30,30,30,29,30,29,31],
    2082: [365, 31,32,31,32,31,30,30,30,29,29,30,30],
    2083: [365, 31,31,32,32,31,30,30,29,30,29,30,30],
}

# Reference point: 2000 BS Baishakh 1 = 1943 April 14 AD
import datetime

_REF_BS = (2000, 1, 1)
_REF_AD = datetime.date(1943, 4, 14)


def _bs_to_days_from_ref(year, month, day):
    """Count days from 2000/1/1 BS to given BS date."""
    total = 0
    for y in range(2000, year):
        if y in _BS_YEAR_DATA:
            total += _BS_YEAR_DATA[y][0]
        else:
            total += 365  # fallback
    if year in _BS_YEAR_DATA:
        months = _BS_YEAR_DATA[year][1:]
        for m in range(1, month):
            total += months[m - 1]
    total += day - 1
    return total


def bs_to_ad(year: int, month: int, day: int) -> datetime.date:
    """Convert Bikram Sambat date to Gregorian."""
    delta = _bs_to_days_from_ref(year, month, day)
    return _REF_AD + datetime.timedelta(days=delta)


def ad_to_bs(date: datetime.date) -> tuple:
    """Convert Gregorian date to (BS year, month, day)."""
    delta = (date - _REF_AD).days
    year = 2000
    while True:
        year_days = _BS_YEAR_DATA.get(year, [365])[0]
        if delta < year_days:
            break
        delta -= year_days
        year += 1
    months = _BS_YEAR_DATA.get(year, [365] + [30] * 12)[1:]
    month = 1
    for m_days in months:
        if delta < m_days:
            break
        delta -= m_days
        month += 1
    day = delta + 1
    return (year, month, day)


_NEPALI_MONTHS = [
    "Baishakh", "Jestha", "Ashadh", "Shrawan",
    "Bhadra", "Ashwin", "Kartik", "Mangsir",
    "Poush", "Magh", "Falgun", "Chaitra",
]


def format_bs(year: int, month: int, day: int, nepali: bool = False) -> str:
    """Format a BS date as string. nepali=True gives month name."""
    if nepali:
        return f"{day} {_NEPALI_MONTHS[month-1]} {year}"
    return f"{year}-{month:02d}-{day:02d}"


def today_bs() -> tuple:
    """Return today's date in BS as (year, month, day)."""
    return ad_to_bs(datetime.date.today())


def today_bs_str(nepali: bool = True) -> str:
    y, m, d = today_bs()
    return format_bs(y, m, d, nepali=nepali)


# ── Template filter ──────────────────────────────────────────────────────────
# Register in your app factory:
#   from smart_mart.utils.nepali_date import ad_to_bs_filter
#   app.jinja_env.filters["bs_date"] = ad_to_bs_filter

def ad_to_bs_filter(ad_date) -> str:
    """Jinja2 filter: {{ sale.created_at | bs_date }}"""
    if ad_date is None:
        return ""
    if isinstance(ad_date, datetime.datetime):
        ad_date = ad_date.date()
    y, m, d = ad_to_bs(ad_date)
    return format_bs(y, m, d, nepali=True)
