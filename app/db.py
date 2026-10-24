from __future__ import annotations

import json
import os
from contextlib import contextmanager
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row


DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://churn:churn@localhost:5432/churn",
)


@contextmanager
def connection() -> Iterator[psycopg.Connection]:
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
        yield conn


def fetch_dataset() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    with connection() as conn:
        accounts = conn.execute(
            'SELECT id, email, balance, "withdrawBalance", "totalReplenished", '
            '"bonusBalance", "bonusState" FROM public.accounts ORDER BY id'
        ).fetchall()
        services = conn.execute(
            'SELECT id, name, "ownerId", "paymentTerm", "autoProlong", '
            '"createdAt", "expiresAt", status, price FROM public.services ORDER BY id'
        ).fetchall()
        transactions = conn.execute(
            'SELECT id, amount, status, "performedAt", "createdAt", type, '
            '"serviceId" FROM public.transactions ORDER BY id'
        ).fetchall()
    return list(accounts), list(services), list(transactions)


def get_settings() -> dict[str, Any]:
    with connection() as conn:
        row = conn.execute(
            "SELECT weights, medium_threshold, high_threshold, horizon_days, updated_at "
            "FROM public.model_settings WHERE id = 1"
        ).fetchone()
    if row is None:
        raise RuntimeError("Model settings row is missing")
    return {
        "weights": row["weights"],
        "medium_threshold": float(row["medium_threshold"]),
        "high_threshold": float(row["high_threshold"]),
        "horizon_days": row["horizon_days"],
        "updated_at": row["updated_at"],
    }


def save_settings(settings: dict[str, Any]) -> dict[str, Any]:
    params = {**settings, "weights": json.dumps(settings["weights"])}
    with connection() as conn:
        row = conn.execute(
            """
            UPDATE public.model_settings
            SET weights = %(weights)s::jsonb,
                medium_threshold = %(medium_threshold)s,
                high_threshold = %(high_threshold)s,
                horizon_days = %(horizon_days)s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = 1
            RETURNING weights, medium_threshold, high_threshold, horizon_days, updated_at
            """,
            params,
        ).fetchone()
    return {
        "weights": row["weights"],
        "medium_threshold": float(row["medium_threshold"]),
        "high_threshold": float(row["high_threshold"]),
        "horizon_days": row["horizon_days"],
        "updated_at": row["updated_at"],
    }


def replace_snapshot(snapshot: dict[str, list[dict[str, Any]]]) -> None:
    with connection() as conn:
        conn.execute(
            "TRUNCATE public.transactions, public.services, public.accounts RESTART IDENTITY"
        )
        conn.executemany(
            """
            INSERT INTO public.accounts
                (id, email, balance, "withdrawBalance", "totalReplenished",
                 "bonusBalance", "bonusState")
            VALUES
                (%(id)s, %(email)s, %(balance)s, %(withdrawBalance)s,
                 %(totalReplenished)s, %(bonusBalance)s, %(bonusState)s)
            """,
            snapshot["accounts"],
        )
        conn.executemany(
            """
            INSERT INTO public.services
                (id, name, "ownerId", "paymentTerm", "autoProlong", "createdAt",
                 "expiresAt", status, price)
            VALUES
                (%(id)s, %(name)s, %(ownerId)s, %(paymentTerm)s, %(autoProlong)s,
                 %(createdAt)s, %(expiresAt)s, %(status)s, %(price)s)
            """,
            snapshot["services"],
        )
        conn.executemany(
            """
            INSERT INTO public.transactions
                (id, amount, status, "performedAt", "createdAt", type, "serviceId")
            VALUES
                (%(id)s, %(amount)s, %(status)s, %(performedAt)s, %(createdAt)s,
                 %(type)s, %(serviceId)s)
            """,
            snapshot["transactions"],
        )
        for table in ("accounts", "services", "transactions"):
            conn.execute(
                f"""
                SELECT setval(
                    pg_get_serial_sequence('public.{table}', 'id'),
                    COALESCE((SELECT max(id) FROM public.{table}), 1),
                    EXISTS (SELECT 1 FROM public.{table})
                )
                """
            )
