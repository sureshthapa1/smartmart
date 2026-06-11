# smart_mart/utils/cash_flow_forecast.py
# ==========================================
# 30-day cash flow forecast using moving averages + Nepal festival seasonality.
# Pure Python, no AI needed.

import datetime
from smart_mart.extensions import db

# Festival season multipliers (month number → revenue multiplier)
# Dashain = Ashwin/Kartik (Oct/Nov) = highest sales
SEASON_MULTIPLIERS = {
    1:  0.85,  # January (Magh) — moderate
    2:  0.80,  # February
    3:  0.90,  # March (Falgun/Holi — slight boost)
    4:  0.85,  # April
    5:  0.80,  # May
    6:  0.75,  # June (hot, slower)
    7:  0.80,  # July (Shrawan — some festivals)
    8:  0.85,  # August
    9:  0.95,  # September (pre-Dashain build-up)
    10: 1.45,  # October (Dashain 🎉)
    11: 1.35,  # November (Tihar 🪔 + post-festival)
    12: 1.10,  # December (corporate gifting + winter demand)
}

# Day-of-week multiplier (0=Monday, 6=Sunday)
DOW_MULTIPLIERS = {
    0: 0.90,  # Monday
    1: 0.95,
    2: 1.00,
    3: 1.00,
    4: 1.05,
    5: 1.15,  # Saturday — busy
    6: 1.20,  # Sunday — busiest retail day Nepal
}


def forecast_cash_flow(days: int = 30) -> list:
    """
    Returns list of dicts:
    {
      'date': datetime.date,
      'projected_revenue': float,
      'projected_expense': float,
      'projected_profit': float,
      'cumulative_profit': float,
      'festival_flag': str or None,
    }
    """
    avg_daily_revenue, avg_daily_expense = _get_averages()
    today      = datetime.date.today()
    cumulative = 0.0
    result     = []

    for i in range(days):
        day  = today + datetime.timedelta(days=i)
        m    = day.month
        dow  = day.weekday()

        season_mult = SEASON_MULTIPLIERS.get(m, 1.0)
        dow_mult    = DOW_MULTIPLIERS.get(dow, 1.0)

        proj_rev = avg_daily_revenue * season_mult * dow_mult
        proj_exp = avg_daily_expense  # expenses are relatively flat
        proj_profit = proj_rev - proj_exp
        cumulative += proj_profit

        result.append({
            "date":               day,
            "projected_revenue":  round(proj_rev, 2),
            "projected_expense":  round(proj_exp, 2),
            "projected_profit":   round(proj_profit, 2),
            "cumulative_profit":  round(cumulative, 2),
            "festival_flag":      _festival_flag(day),
        })

    return result


def _get_averages():
    """Calculate 30-day rolling averages from actual DB data."""
    try:
        cutoff = datetime.date.today() - datetime.timedelta(days=30)
        rev_row = db.session.execute(
            db.text("SELECT COALESCE(AVG(daily_rev), 0) FROM "
                    "(SELECT DATE(created_at) d, SUM(total_amount) daily_rev "
                    " FROM sales WHERE DATE(created_at) >= :c GROUP BY DATE(created_at)) t"),
            {"c": cutoff},
        ).fetchone()
        exp_row = db.session.execute(
            db.text("SELECT COALESCE(AVG(daily_exp), 0) FROM "
                    "(SELECT DATE(created_at) d, SUM(amount) daily_exp "
                    " FROM expenses WHERE DATE(created_at) >= :c GROUP BY DATE(created_at)) t"),
            {"c": cutoff},
        ).fetchone()
        return float(rev_row[0] or 0), float(exp_row[0] or 0)
    except Exception:
        return 5000.0, 1500.0   # fallback defaults if DB query fails


FESTIVALS_2082 = {
    # Approximate BS→AD dates for 2082 BS (2025-26 AD)
    datetime.date(2025, 10, 2):  "Dashain (Ghatasthapana)",
    datetime.date(2025, 10, 10): "Dashain (Vijaya Dashami) 🎉",
    datetime.date(2025, 10, 20): "Tihar (Laxmi Puja) 🪔",
    datetime.date(2025, 10, 22): "Tihar (Bhai Tika) 🪔",
    datetime.date(2026, 1, 14):  "Maghe Sankranti",
    datetime.date(2026, 3, 8):   "Holi / Fagu Purnima",
}


def _festival_flag(date: datetime.date) -> str | None:
    return FESTIVALS_2082.get(date)
