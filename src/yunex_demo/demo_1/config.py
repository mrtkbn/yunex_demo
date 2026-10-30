"""Central configuration for the FX Risk & Anomaly Monitor."""

import os

from dotenv import load_dotenv

load_dotenv()

API = "https://api.frankfurter.dev/v2"

# MinIO connection, read from the environment (.env). Credentials have no defaults.
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")
MINIO_SECURE = os.getenv("MINIO_SECURE", "false").lower() == "true"
MINIO_USER = os.getenv("MINIO_USER")
MINIO_PASSWORD = os.getenv("MINIO_PASSWORD")
BUCKETS = ("raw", "silver", "gold")

# Date window (about 18 months of daily data)
START = os.getenv("FX_START", "2025-01-01")
END = os.getenv("FX_END", "2026-10-30")

# ECB reference rates: 1 EUR = x <currency>
ECB_BASE = "EUR"
ECB_QUOTES = ["USD", "PLN", "GBP", "CHF", "TRY", "CZK", "JPY"]

# CBAR official rates, requested with base=AZN: 1 AZN = x <currency>
# (we invert to "AZN per 1 unit" in the transform step)
CBAR_BASE = "AZN"
CBAR_QUOTES = ["USD", "EUR", "TRY", "PLN", "GBP", "CHF", "CZK", "JPY"]

# Analytics parameters
WINDOW = 30  # rolling window (trading days)
Z_THRESHOLD = 3.0  # |z| above this = anomaly
TRADING_DAYS = 252  # for annualising volatility
VAR_WINDOW = 90  # window for historical 1-day Value-at-Risk (95%)
GAP_ALERT_PCT = 1.0  # ECB vs CBAR cross-rate gap above this % = alert

# Risk for a payment term (used by the app, see horizon.py)
HORIZON_LOW = 0.02  # 1-in-20 downside below 2% of the invoice  -> Low
HORIZON_HIGH = 0.05  # 5% or more                                 -> High (in between: Medium)
HORIZON_MIN_WINDOWS = 60  # past periods needed before trusting the historical estimate
HORIZON_MIN_OBS = 30  # daily moves needed inside a chosen date range
HORIZON_TERMS = [7, 14, 30, 60, 90, 180]  # payment terms (calendar days) shown in the table

# Reference data for dim_currency
CURRENCY_INFO = {
    "EUR": ("Euro", "Eurozone", "Floating", "Major"),
    "USD": ("US Dollar", "North America", "Floating", "Major"),
    "GBP": ("Pound Sterling", "Europe", "Floating", "Major"),
    "CHF": ("Swiss Franc", "Europe", "Floating", "Safe haven"),
    "JPY": ("Japanese Yen", "Asia", "Floating", "Major"),
    "PLN": ("Polish Zloty", "Europe", "Floating", "Emerging"),
    "CZK": ("Czech Koruna", "Europe", "Floating", "Emerging"),
    "TRY": ("Turkish Lira", "Europe/Asia", "Managed float", "Emerging"),
    "AZN": ("Azerbaijani Manat", "Caucasus", "Pegged to USD (1.70)", "Emerging"),
}

PROVIDERS = {
    "ECB": ("European Central Bank", "Euro area", "Reference rate used for EU VAT and customs"),
    "CBAR": ("Central Bank of Azerbaijan", "Azerbaijan", "Official manat rate for tax, customs and contracts"),
}
