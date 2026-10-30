"""Tiny storage layer: MinIO by default, local ./data folder as fallback."""

import io
import os
from pathlib import Path

import pandas as pd
from config import BUCKETS, MINIO_ENDPOINT, MINIO_PASSWORD, MINIO_SECURE, MINIO_USER

LOCAL_ROOT = Path(__file__).resolve().parent.parent / "data"


class Store:
    def __init__(self, local: bool = False):
        self.local = local
        if local:
            for b in BUCKETS:
                (LOCAL_ROOT / b).mkdir(parents=True, exist_ok=True)
            self.client = None
        else:
            from minio import Minio

            if not MINIO_USER or not MINIO_PASSWORD:
                raise RuntimeError("Set MINIO_USER and MINIO_PASSWORD in .env (see .env.example)")
            self.client = Minio(MINIO_ENDPOINT, MINIO_USER, MINIO_PASSWORD, secure=MINIO_SECURE)
            for b in BUCKETS:
                if not self.client.bucket_exists(b):
                    self.client.make_bucket(b)

    # ---- bytes -----------------------------------------------------
    def put_bytes(self, bucket: str, key: str, data: bytes) -> str:
        if self.local:
            path = LOCAL_ROOT / bucket / key
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        else:
            self.client.put_object(bucket, key, io.BytesIO(data), len(data))
        return f"{bucket}/{key}"

    def get_bytes(self, bucket: str, key: str) -> bytes:
        if self.local:
            return (LOCAL_ROOT / bucket / key).read_bytes()
        resp = self.client.get_object(bucket, key)
        try:
            return resp.read()
        finally:
            resp.close()
            resp.release_conn()

    # ---- dataframes ------------------------------------------------
    def put_parquet(self, bucket: str, key: str, df: pd.DataFrame) -> str:
        buf = io.BytesIO()
        df.to_parquet(buf, index=False)
        return self.put_bytes(bucket, key, buf.getvalue())

    def get_parquet(self, bucket: str, key: str) -> pd.DataFrame:
        return pd.read_parquet(io.BytesIO(self.get_bytes(bucket, key)))

    def get_csv(self, bucket: str, key: str) -> pd.DataFrame:
        return pd.read_csv(io.BytesIO(self.get_bytes(bucket, key)), parse_dates=["date"])


def default_local() -> bool:
    """Use local files when FX_LOCAL=1 is set."""
    return os.getenv("FX_LOCAL", "0") == "1"
