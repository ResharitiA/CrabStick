from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginRequest(StrictModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)


class AccountInput(StrictModel):
    id: int = Field(gt=0)
    email: str = Field(min_length=3, max_length=320)
    balance: int = Field(ge=0)
    withdrawBalance: int = Field(ge=0)
    totalReplenished: int = Field(ge=0)
    bonusBalance: int = Field(ge=0)
    bonusState: Literal["locked", "unlocked"]


class ServiceInput(StrictModel):
    id: int = Field(gt=0)
    name: str = Field(pattern=r"^[A-Za-z0-9-]+$")
    ownerId: int = Field(gt=0)
    paymentTerm: Literal["hour", "week", "month", "quarter_year", "half_year", "year"]
    autoProlong: bool
    createdAt: datetime
    expiresAt: datetime
    status: Literal["active", "blocked", "deleted", "suspended", "prolong_wait"]
    price: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_dates(self) -> "ServiceInput":
        if self.createdAt.utcoffset() is None or self.expiresAt.utcoffset() is None:
            raise ValueError("createdAt and expiresAt must include a timezone")
        if self.expiresAt < self.createdAt:
            raise ValueError("expiresAt must not be earlier than createdAt")
        return self


class TransactionInput(StrictModel):
    id: int = Field(gt=0)
    amount: int = Field(ge=0)
    status: Literal["performed", "not_performed"]
    performedAt: datetime | None
    createdAt: datetime
    type: Literal["prolong", "order"]
    serviceId: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_performed_at(self) -> "TransactionInput":
        if self.createdAt.utcoffset() is None:
            raise ValueError("createdAt must include a timezone")
        if self.performedAt is not None and self.performedAt.utcoffset() is None:
            raise ValueError("performedAt must include a timezone")
        if self.status == "performed":
            if self.performedAt is None or self.performedAt < self.createdAt:
                raise ValueError("performed transaction needs performedAt >= createdAt")
        elif self.performedAt is not None:
            raise ValueError("not_performed transaction must not have performedAt")
        return self


class SnapshotInput(StrictModel):
    accounts: list[AccountInput]
    services: list[ServiceInput]
    transactions: list[TransactionInput]

    @model_validator(mode="after")
    def validate_relations(self) -> "SnapshotInput":
        account_ids = [item.id for item in self.accounts]
        service_ids = [item.id for item in self.services]
        transaction_ids = [item.id for item in self.transactions]
        if len(account_ids) != len(set(account_ids)):
            raise ValueError("account ids must be unique")
        if len(service_ids) != len(set(service_ids)):
            raise ValueError("service ids must be unique")
        if len(transaction_ids) != len(set(transaction_ids)):
            raise ValueError("transaction ids must be unique")
        if any(item.ownerId not in set(account_ids) for item in self.services):
            raise ValueError("every service ownerId must reference an account")
        if any(item.serviceId not in set(service_ids) for item in self.transactions):
            raise ValueError("every transaction serviceId must reference a service")
        return self


class ModelSettingsPatch(StrictModel):
    weights: dict[str, float] | None = None
    medium_threshold: float | None = Field(default=None, ge=0, le=100)
    high_threshold: float | None = Field(default=None, ge=0, le=100)
    horizon_days: int | None = Field(default=None, ge=1, le=365)

    @model_validator(mode="after")
    def validate_weights(self) -> "ModelSettingsPatch":
        if self.weights is not None:
            if not self.weights:
                raise ValueError("weights must not be empty")
            if any(value < 0 or value > 100 for value in self.weights.values()):
                raise ValueError("weight values must be between 0 and 100")
        return self
