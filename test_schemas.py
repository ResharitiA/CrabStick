import unittest
from datetime import datetime, timezone

from pydantic import ValidationError

from app.schemas import ServiceInput, TransactionInput


class InputSchemaTests(unittest.TestCase):
    def test_transaction_accepts_amount_above_fixture_range(self):
        transaction = TransactionInput(
            id=1,
            amount=250_000,
            status="performed",
            performedAt=datetime(2026, 10, 24, 12, 1, tzinfo=timezone.utc),
            createdAt=datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc),
            type="prolong",
            serviceId=1,
        )
        self.assertEqual(transaction.amount, 250_000)

    def test_service_rejects_dates_without_timezone(self):
        with self.assertRaises(ValidationError):
            ServiceInput(
                id=1,
                name="service-1",
                ownerId=1,
                paymentTerm="month",
                autoProlong=True,
                createdAt=datetime(2026, 10, 24, 12, 0),
                expiresAt=datetime(2026, 11, 24, 12, 0),
                status="active",
                price=1000,
            )

    def test_transaction_rejects_mixed_timezone_dates(self):
        with self.assertRaises(ValidationError):
            TransactionInput(
                id=1,
                amount=1000,
                status="performed",
                performedAt=datetime(2026, 10, 24, 12, 1),
                createdAt=datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc),
                type="prolong",
                serviceId=1,
            )


if __name__ == "__main__":
    unittest.main()
