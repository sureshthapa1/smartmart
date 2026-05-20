from datetime import date, timedelta


BS_MONTHS = [
    "Baishakh", "Jestha", "Ashadh", "Shrawan", "Bhadra", "Ashwin",
    "Kartik", "Mangsir", "Poush", "Magh", "Falgun", "Chaitra",
]

_EPOCH_AD = date(2025, 4, 14)
_EPOCH_BS = (2082, 1, 1)
_BS_MONTH_DAYS = {
    2081: [31, 31, 32, 31, 31, 31, 30, 29, 30, 29, 30, 30],
    2082: [31, 32, 31, 32, 31, 30, 30, 29, 30, 29, 30, 30],
    2083: [31, 31, 32, 31, 31, 30, 30, 30, 29, 30, 29, 31],
    2084: [31, 31, 32, 31, 31, 30, 30, 30, 29, 30, 30, 30],
}


def _month_days(year, month):
    return _BS_MONTH_DAYS.get(year, _BS_MONTH_DAYS[2083])[(month - 1) % 12]


def ad_to_bs(ad_date):
    if hasattr(ad_date, "date"):
        ad_date = ad_date.date()
    delta_days = (ad_date - _EPOCH_AD).days
    year, month, day = _EPOCH_BS

    if delta_days >= 0:
        for _ in range(delta_days):
            day += 1
            if day > _month_days(year, month):
                day = 1
                month += 1
                if month > 12:
                    month = 1
                    year += 1
    else:
        for _ in range(abs(delta_days)):
            day -= 1
            if day < 1:
                month -= 1
                if month < 1:
                    month = 12
                    year -= 1
                day = _month_days(year, month)

    return year, month, day


def format_bs(year, month, day):
    month_name = BS_MONTHS[month - 1]
    return f"{day} {month_name} {year} BS"


def today_bs_str():
    return format_bs(*ad_to_bs(date.today()))


def bs_month_name(ad_date=None):
    ad_date = ad_date or date.today()
    _, month, _ = ad_to_bs(ad_date)
    return BS_MONTHS[month - 1]
