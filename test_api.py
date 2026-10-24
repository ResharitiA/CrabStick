import copy
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.risk import DEFAULT_WEIGHTS


NOW = datetime(2026, 10, 24, tzinfo=timezone.utc)
ACCOUNTS = [
    {
        "id": 1,
        "email": "high@example.test",
        "balance": 0,
        "withdrawBalance": 0,
        "totalReplenished": 5000,
        "bonusBalance": 0,
        "bonusState": "locked",
    },
    {
        "id": 2,
        "email": "low@example.test",
        "balance": 5000,
        "withdrawBalance": 0,
        "totalReplenished": 9000,
        "bonusBalance": 0,
        "bonusState": "locked",
    },
]
SERVICES = [
    {
        "id": 1,
        "name": "service-1",
        "ownerId": 1,
        "paymentTerm": "month",
        "autoProlong": False,
        "createdAt": NOW - timedelta(days=30),
        "expiresAt": NOW - timedelta(days=1),
        "status": "prolong_wait",
        "price": 2000,
    },
    {
        "id": 2,
        "name": "service-2",
        "ownerId": 2,
        "paymentTerm": "month",
        "autoProlong": True,
        "createdAt": NOW - timedelta(days=5),
        "expiresAt": NOW + timedelta(days=25),
        "status": "active",
        "price": 1000,
    },
]
TRANSACTIONS = [
    {
        "id": 1,
        "amount": 1000,
        "status": "not_performed",
        "performedAt": None,
        "createdAt": NOW - timedelta(days=1),
        "type": "prolong",
        "serviceId": 1,
    }
]
SETTINGS = {
    "weights": DEFAULT_WEIGHTS,
    "medium_threshold": 35.0,
    "high_threshold": 65.0,
    "horizon_days": 30,
    "updated_at": NOW,
}


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.dataset_patch = patch(
            "app.main.fetch_dataset",
            return_value=(ACCOUNTS, SERVICES, TRANSACTIONS),
        )
        self.settings_patch = patch(
            "app.main.get_settings",
            side_effect=lambda: copy.deepcopy(SETTINGS),
        )
        self.dataset_patch.start()
        self.settings_patch.start()
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.settings_patch.stop()
        self.dataset_patch.stop()

    def login(self):
        response = self.client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "change-me"},
        )
        self.assertEqual(response.status_code, 200)

    def test_data_api_requires_authentication(self):
        self.assertEqual(self.client.get("/api/v1/customers").status_code, 401)
        self.assertEqual(self.client.get("/api/v1/model/settings").status_code, 401)
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.url.path.endswith("/login"))
        self.assertEqual(self.client.get("/static/index.html").status_code, 404)

    def test_login_sorting_summary_and_metrics(self):
        self.login()
        response = self.client.get(
            "/api/v1/customers?sort_by=balance&sort_order=asc"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["id"] for item in response.json()["items"]], [1, 2])
        self.assertGreater(
            response.json()["items"][0]["score"],
            response.json()["items"][1]["score"],
        )

        summary = self.client.get("/api/v1/analytics/summary").json()
        self.assertEqual(summary["customers_total"], 2)
        self.assertGreater(summary["potential_loss"], 0)

        metrics = self.client.get("/metrics")
        self.assertEqual(metrics.status_code, 200)
        self.assertIn("churn_customers", metrics.text)
        self.assertIn("churn_potential_loss_euro_cents", metrics.text)

    def test_settings_patch_merges_weights_and_rejects_unknown_keys(self):
        self.login()
        with patch(
            "app.main.save_settings",
            side_effect=lambda value: {**value, "updated_at": NOW},
        ):
            response = self.client.patch(
                "/api/v1/model/settings",
                json={"weights": {"blocked": 40}},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["weights"]["blocked"], 40)
        self.assertEqual(
            response.json()["weights"]["expiring_soon"],
            DEFAULT_WEIGHTS["expiring_soon"],
        )

        invalid = self.client.patch(
            "/api/v1/model/settings",
            json={"weights": {"unknown_signal": 10}},
        )
        self.assertEqual(invalid.status_code, 422)

    def test_logout_ends_session(self):
        self.login()
        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, 200)
        self.assertEqual(
            self.client.post("/api/v1/auth/logout").status_code,
            204,
        )
        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, 401)


if __name__ == "__main__":
    unittest.main()
