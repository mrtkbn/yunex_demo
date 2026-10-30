"""Step 1 - INGEST: download raw rates from Frankfurter v2 and land them in the RAW bucket.

Usage:
    python src/ingest.py             # live API -> MinIO
    python src/ingest.py --local      # live API -> ./data (no MinIO needed)
    python src/ingest.py --offline    # synthetic sample data (no internet needed)
"""

import argparse
import io

import config as C
import pandas as pd
import requests
from sample_data import make_sample
from storage import Store, default_local


def fetch_csv(provider: str, base: str, quotes: list[str]) -> bytes:
    """One provider-pinned CSV request. Columns: date, base, quote, rate."""
    url = f"{C.API}/providers/{provider.lower()}/rates.csv"
    params = {"base": base, "quotes": ",".join(quotes), "from": C.START, "to": C.END}
    r = requests.get(url, params=params, timeout=60)
    r.raise_for_status()
    return r.content


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", action="store_true", help="write to ./data instead of MinIO")
    ap.add_argument("--offline", action="store_true", help="use synthetic data, skip the API")
    args = ap.parse_args()

    store = Store(local=args.local or args.offline or default_local())
    stamp = f"{C.START}_{C.END}"

    if args.offline:
        ecb_df, cbar_df = make_sample(C.START, C.END)
        ecb_raw = ecb_df.to_csv(index=False).encode()
        cbar_raw = cbar_df.to_csv(index=False).encode()
        print("Using synthetic sample data")
    else:
        ecb_raw = fetch_csv("ecb", C.ECB_BASE, C.ECB_QUOTES)
        cbar_raw = fetch_csv("cbar", C.CBAR_BASE, C.CBAR_QUOTES)

    for name, raw in (("ecb", ecb_raw), ("cbar", cbar_raw)):
        key = f"{name}/rates_{stamp}.csv"
        store.put_bytes("raw", key, raw)
        rows = len(pd.read_csv(io.BytesIO(raw)))
        print(f"raw/{key}: {rows} rows")


if __name__ == "__main__":
    main()
