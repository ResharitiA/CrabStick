from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

DEFAULT_WEIGHTS = {
    "expiring_soon": 18.0,
    "autoprolong_disabled": 12.0,
    "prolong_wait": 30.0,
    "insufficient_funds": 25.0,
    "uncovered_cost": 20.0,
    "failed_prolong": 16.0,
    "no_successful_prolong": 8.0,
    "suspended": 10.0,
    "blocked": 25.0,
    "deleted": 20.0,
}

FEATURE_LABELS = {
    "expiring_soon": "Услуги скоро заканчиваются",
    "autoprolong_disabled": "Автопродление отключено",
    "prolong_wait": "Есть услуги в ожидании оплаты",
    "insufficient_funds": "Недостаточно доступных средств",
    "uncovered_cost": "Стоимость продлений не покрыта балансом",
    "failed_prolong": "Есть неуспешные транзакции продления",
    "no_successful_prolong": "Нет успешных продлений",
    "suspended": "Есть приостановленные услуги",
    "blocked": "Есть заблокированные услуги",
    "deleted": "Есть удалённые услуги",
}

RENEWABLE_STATUSES = {"active", "suspended", "prolong_wait"}


def parse_datetime(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def calculate_customer_risk(
    account: dict[str, Any],
    services: list[dict[str, Any]],
    transactions: list[dict[str, Any]],
    settings: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if not services:
        return {
            "customer_id": account["id"],
            "score": None,
            "risk_level": "not_assessed",
            "loyalty_score": None,
            "reasons": ["Недостаточно данных: у клиента нет услуг"],
            "features": [],
            "potential_loss": 0,
            "currency": "EUR",
            "data_sufficient": False,
            "exposure": 0,
            "spendable_balance": spendable_balance(account),
        }

    horizon_days = int(settings["horizon_days"])
    weights = {**DEFAULT_WEIGHTS, **settings.get("weights", {})}
    spendable = spendable_balance(account)
    service_count = len(services)
    statuses = [service["status"] for service in services]

    upcoming = []
    proximities = []
    autoprolong_disabled = 0
    for service in services:
        if service["status"] not in RENEWABLE_STATUSES:
            continue
        days = (parse_datetime(service["expiresAt"]) - now).total_seconds() / 86400
        if service["status"] == "prolong_wait" or days <= horizon_days:
            upcoming.append(service)
            proximities.append(1.0 if days <= 0 else max(0.0, 1.0 - days / horizon_days))
            if not service["autoProlong"]:
                autoprolong_disabled += 1

    exposure = sum(int(service["price"]) for service in upcoming)
    uncovered = max(exposure - spendable, 0)
    prolong_transactions = [tx for tx in transactions if tx["type"] == "prolong"]
    failed_prolong = [tx for tx in prolong_transactions if tx["status"] == "not_performed"]
    successful_prolong = [tx for tx in prolong_transactions if tx["status"] == "performed"]

    values = {
        "expiring_soon": max(proximities, default=0.0),
        "autoprolong_disabled": autoprolong_disabled / len(upcoming) if upcoming else 0.0,
        "prolong_wait": statuses.count("prolong_wait") / service_count,
        "insufficient_funds": 1.0 if exposure > 0 and spendable < exposure else 0.0,
        "uncovered_cost": uncovered / exposure if exposure else 0.0,
        "failed_prolong": len(failed_prolong) / len(prolong_transactions) if prolong_transactions else 0.0,
        "no_successful_prolong": 1.0 if upcoming and not successful_prolong else 0.0,
        "suspended": statuses.count("suspended") / service_count,
        "blocked": statuses.count("blocked") / service_count,
        "deleted": statuses.count("deleted") / service_count,
    }

    features = []
    for key, value in values.items():
        contribution = round(float(weights[key]) * value, 2)
        features.append(
            {
                "key": key,
                "label": FEATURE_LABELS[key],
                "value": round(value, 4),
                "weight": float(weights[key]),
                "contribution": contribution,
            }
        )

    score = round(min(100.0, sum(item["contribution"] for item in features)), 2)
    if score >= float(settings["high_threshold"]):
        level = "high"
    elif score >= float(settings["medium_threshold"]):
        level = "medium"
    else:
        level = "low"

    coverage = min(spendable / exposure, 1.0) if exposure else 1.0
    potential_loss = round(exposure * (score / 100) * (1 - 0.5 * coverage))
    reasons = [
        f'{item["label"]}: +{item["contribution"]:.2f}'
        for item in sorted(features, key=lambda item: item["contribution"], reverse=True)
        if item["contribution"] > 0
    ][:5]
    if not reasons:
        reasons = ["Значимые сигналы риска не обнаружены"]

    return {
        "customer_id": account["id"],
        "score": score,
        "risk_level": level,
        "loyalty_score": round(100 - score, 2),
        "reasons": reasons,
        "features": features,
        "potential_loss": potential_loss,
        "currency": "EUR",
        "data_sufficient": True,
        "exposure": exposure,
        "spendable_balance": spendable,
    }


def spendable_balance(account: dict[str, Any]) -> int:
    bonus = int(account["bonusBalance"]) if account["bonusState"] == "unlocked" else 0
    return int(account["balance"]) + bonus
