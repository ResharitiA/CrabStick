import unittest
from datetime import datetime, timedelta, timezone

from app.risk import DEFAULT_WEIGHTS, calculate_customer_risk


NOW = datetime(2026, 10, 24, tzinfo=timezone.utc)
SETTINGS = {
    "weights": DEFAULT_WEIGHTS,
    "medium_threshold": 35,
    "high_threshold": 65,
    "horizon_days": 30,
}


def account(balance=0, bonus=0, bonus_state="locked"):
    return {
        "id": 1,
        "email": "customer@example.test",
        "balance": balance,
        "withdrawBalance": 0,
        "totalReplenished": balance,
        "bonusBalance": bonus,
        "bonusState": bonus_state,
    }


def service(**overrides):
    values = {
        "id": 1,
        "ownerId": 1,
        "name": "service-1",
        "paymentTerm": "month",
        "autoProlong": False,
        "createdAt": NOW - timedelta(days=20),
        "expiresAt": NOW + timedelta(days=2),
        "status": "active",
        "price": 1000,
    }
    values.update(overrides)
    return values


class RiskModelTests(unittest.TestCase):
    def test_no_services_is_not_assessed(self):
        result = calculate_customer_risk(account(), [], [], SETTINGS, NOW)
        self.assertFalse(result["data_sufficient"])
        self.assertIsNone(result["score"])
        self.assertEqual(result["risk_level"], "not_assessed")

    def test_risk_increases_for_waiting_unfunded_service(self):
        safe = calculate_customer_risk(
            account(balance=1000),
            [service(autoProlong=True, expiresAt=NOW + timedelta(days=29))],
            [],
            SETTINGS,
            NOW,
        )
        risky = calculate_customer_risk(
            account(),
            [service(status="prolong_wait", expiresAt=NOW - timedelta(days=1))],
            [{"type": "prolong", "status": "not_performed"}],
            SETTINGS,
            NOW,
        )
        self.assertGreater(risky["score"], safe["score"])
        self.assertGreater(risky["potential_loss"], safe["potential_loss"])

    def test_unlocked_bonus_covers_renewal(self):
        locked = calculate_customer_risk(
            account(bonus=1000), [service(autoProlong=True)], [], SETTINGS, NOW
        )
        unlocked = calculate_customer_risk(
            account(bonus=1000, bonus_state="unlocked"),
            [service(autoProlong=True)],
            [],
            SETTINGS,
            NOW,
        )
        locked_feature = next(
            item for item in locked["features"] if item["key"] == "insufficient_funds"
        )
        unlocked_feature = next(
            item for item in unlocked["features"] if item["key"] == "insufficient_funds"
        )
        self.assertEqual(locked_feature["value"], 1)
        self.assertEqual(unlocked_feature["value"], 0)

    def test_custom_weight_changes_score(self):
        low = calculate_customer_risk(account(), [service()], [], SETTINGS, NOW)
        custom = {**SETTINGS, "weights": {**DEFAULT_WEIGHTS, "autoprolong_disabled": 40}}
        high = calculate_customer_risk(account(), [service()], [], custom, NOW)
        self.assertGreater(high["score"], low["score"])

    def test_missing_prolong_is_not_risk_before_forecast_horizon(self):
        result = calculate_customer_risk(
            account(balance=1000),
            [service(autoProlong=True, expiresAt=NOW + timedelta(days=60))],
            [],
            SETTINGS,
            NOW,
        )
        feature = next(
            item for item in result["features"] if item["key"] == "no_successful_prolong"
        )
        self.assertEqual(feature["value"], 0)


if __name__ == "__main__":
    unittest.main()
