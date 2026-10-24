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


def simulate_dataset_step() -> None:
    with connection() as conn:
        conn.execute(
            """
            WITH simulation_clock AS (
                SELECT floor(extract(epoch FROM clock_timestamp()) / 10)::bigint AS tick
            )
            UPDATE public.accounts AS account
            SET balance = CASE
                    WHEN mod(account.id + simulation_clock.tick, 5) = 1
                        THEN mod(account.id + simulation_clock.tick, 3) * 100
                    WHEN mod(account.id + simulation_clock.tick, 4) = 0
                        THEN account.balance + 1200
                    WHEN mod(account.id + simulation_clock.tick, 4) = 1
                        THEN CASE WHEN account.balance >= 900 THEN account.balance - 900 ELSE account.balance + 450 END
                    WHEN mod(account.id + simulation_clock.tick, 4) = 2
                        THEN account.balance + 350
                    ELSE CASE WHEN account.balance >= 200 THEN account.balance - 200 ELSE account.balance + 250 END
                END,
                "withdrawBalance" = account."withdrawBalance" + mod(account.id + simulation_clock.tick, 5) * 10,
                "totalReplenished" = account."totalReplenished"
                    + CASE WHEN mod(account.id + simulation_clock.tick, 3) = 0 THEN 750 ELSE 100 END,
                "bonusBalance" = account."bonusBalance" + mod(account.id + simulation_clock.tick, 4) * 25,
                "bonusState" = CASE
                    WHEN mod(account.id + simulation_clock.tick, 5) = 1 THEN 'locked'
                    WHEN mod(account.id + simulation_clock.tick, 2) = 0 THEN 'unlocked'
                    ELSE 'locked'
                END
            FROM simulation_clock
            """
        )
        conn.execute(
            """
            WITH simulation_clock AS (
                SELECT floor(extract(epoch FROM clock_timestamp()) / 10)::bigint AS tick
            ), next_values AS (
                SELECT service.id,
                       mod(service.id + simulation_clock.tick, 5) AS phase
                FROM public.services AS service
                CROSS JOIN simulation_clock
            )
            UPDATE public.services AS service
            SET status = CASE next_values.phase
                    WHEN 0 THEN 'active'
                    WHEN 1 THEN 'prolong_wait'
                    WHEN 2 THEN 'suspended'
                    WHEN 3 THEN 'blocked'
                    ELSE 'deleted'
                END,
                "autoProlong" = CASE
                    WHEN next_values.phase IN (1, 2) THEN false
                    ELSE mod(service.id + simulation_clock.tick, 3) = 0
                END,
                "expiresAt" = CASE next_values.phase
                    WHEN 0 THEN GREATEST(service."createdAt", clock_timestamp() + interval '45 days')
                    WHEN 1 THEN GREATEST(service."createdAt", clock_timestamp() - interval '1 day')
                    WHEN 2 THEN GREATEST(service."createdAt", clock_timestamp() + interval '7 days')
                    ELSE GREATEST(service."createdAt", clock_timestamp())
                END
            FROM next_values, simulation_clock
            WHERE service.id = next_values.id
            """
        )
        conn.execute(
            """
            WITH simulation_clock AS (
                SELECT floor(extract(epoch FROM clock_timestamp()) / 10)::bigint AS tick
            )
            UPDATE public.transactions AS tx
            SET status = CASE WHEN mod(tx.id + simulation_clock.tick, 3) = 0
                    THEN 'not_performed' ELSE 'performed' END,
                "performedAt" = CASE WHEN mod(tx.id + simulation_clock.tick, 3) = 0
                    THEN NULL ELSE GREATEST(tx."createdAt", clock_timestamp()) END
            FROM simulation_clock
            """
        )


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
