from __future__ import annotations

import os
import secrets
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

from app.db import fetch_dataset, get_settings, replace_snapshot, save_settings
from app.risk import DEFAULT_WEIGHTS, calculate_customer_risk
from app.schemas import LoginRequest, ModelSettingsPatch, SnapshotInput


BASE_DIR = Path(__file__).resolve().parent
APP_USERNAME = os.getenv("APP_USERNAME", "admin")
APP_PASSWORD = os.getenv("APP_PASSWORD", "change-me")
SESSION_SECRET = os.getenv("SESSION_SECRET", "development-secret-change-me")

app = FastAPI(
    title="Анализатор рисков оттока",
    version="1.0.0",
    docs_url="/api/v1/docs",
    openapi_url="/api/v1/openapi.json",
)
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    same_site="lax",
    https_only=os.getenv("COOKIE_SECURE", "false").lower() == "true",
    max_age=8 * 60 * 60,
)

def require_user(request: Request) -> str:
    username = request.session.get("username")
    if not username:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    return str(username)


def analytics() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    accounts, services, transactions = fetch_dataset()
    settings = get_settings()
    services_by_owner: dict[int, list[dict[str, Any]]] = defaultdict(list)
    transactions_by_service: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for service in services:
        services_by_owner[service["ownerId"]].append(service)
    for transaction in transactions:
        transactions_by_service[transaction["serviceId"]].append(transaction)

    customers = []
    for account in accounts:
        customer_services = services_by_owner[account["id"]]
        customer_transactions = [
            transaction
            for service in customer_services
            for transaction in transactions_by_service[service["id"]]
        ]
        risk = calculate_customer_risk(
            account, customer_services, customer_transactions, settings
        )
        customers.append(
            {
                **account,
                **risk,
                "service_count": len(customer_services),
            }
        )
    return customers, settings


@app.get("/health", include_in_schema=False)
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/login", response_class=HTMLResponse, include_in_schema=False)
def login_page(request: Request) -> Response:
    if request.session.get("username"):
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    return FileResponse(BASE_DIR / "static" / "login.html")


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def dashboard(request: Request) -> Response:
    if not request.session.get("username"):
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.post("/api/v1/auth/login")
def login(payload: LoginRequest, request: Request) -> dict[str, str]:
    valid_username = secrets.compare_digest(payload.username, APP_USERNAME)
    valid_password = secrets.compare_digest(payload.password, APP_PASSWORD)
    if not (valid_username and valid_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )
    request.session.clear()
    request.session["username"] = APP_USERNAME
    return {"username": APP_USERNAME}


@app.get("/api/v1/auth/me")
def me(username: str = Depends(require_user)) -> dict[str, str]:
    return {"username": username}


@app.post("/api/v1/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, _: str = Depends(require_user)) -> Response:
    request.session.clear()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/api/v1/customers")
def list_customers(
    _: str = Depends(require_user),
    sort_by: Literal["id", "email", "balance", "totalReplenished", "score"] = "score",
    sort_order: Literal["asc", "desc"] = "desc",
    risk_level: Literal["high", "medium", "low", "not_assessed"] | None = None,
    search: str | None = Query(default=None, max_length=200),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    customers, _settings = analytics()
    if risk_level:
        customers = [item for item in customers if item["risk_level"] == risk_level]
    if search:
        needle = search.casefold()
        customers = [
            item
            for item in customers
            if needle in item["email"].casefold() or needle in str(item["id"])
        ]

    def key(item: dict[str, Any]) -> tuple[bool, Any]:
        value = item[sort_by]
        return value is None, value if value is not None else 0

    customers.sort(key=key, reverse=sort_order == "desc")
    return {
        "items": customers[offset : offset + limit],
        "total": len(customers),
        "offset": offset,
        "limit": limit,
    }


@app.get("/api/v1/customers/{customer_id}")
def customer_detail(
    customer_id: int,
    _: str = Depends(require_user),
) -> dict[str, Any]:
    accounts, services, transactions = fetch_dataset()
    account = next((item for item in accounts if item["id"] == customer_id), None)
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer not found")
    customer_services = [item for item in services if item["ownerId"] == customer_id]
    service_ids = {item["id"] for item in customer_services}
    customer_transactions = [
        item for item in transactions if item["serviceId"] in service_ids
    ]
    risk = calculate_customer_risk(
        account, customer_services, customer_transactions, get_settings()
    )
    return {
        "account": account,
        "risk": risk,
        "services": customer_services,
        "transactions": customer_transactions,
    }


@app.get("/api/v1/analytics/summary")
def summary(_: str = Depends(require_user)) -> dict[str, Any]:
    customers, settings = analytics()
    counts = Counter(item["risk_level"] for item in customers)
    return {
        "customers_total": len(customers),
        "risk_counts": {
            level: counts.get(level, 0)
            for level in ("high", "medium", "low", "not_assessed")
        },
        "potential_loss": sum(item["potential_loss"] for item in customers),
        "currency": "EUR",
        "money_unit": "euro_cent",
        "model_updated_at": settings["updated_at"],
        "explanation": (
            "Потери — сумма ожидаемой выручки ближайших продлений, умноженная "
            "на риск клиента; доступный основной и разблокированный бонусный "
            "баланс уменьшают оценку максимум на 50%."
        ),
    }


@app.get("/api/v1/model/settings")
def model_settings(_: str = Depends(require_user)) -> dict[str, Any]:
    return get_settings()


@app.patch("/api/v1/model/settings")
def patch_model_settings(
    payload: ModelSettingsPatch,
    _: str = Depends(require_user),
) -> dict[str, Any]:
    current = get_settings()
    patch = payload.model_dump(exclude_none=True)
    supplied_weights = patch.pop("weights", None)
    if supplied_weights is not None:
        unknown = set(supplied_weights) - set(DEFAULT_WEIGHTS)
        if unknown:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unknown weight keys: {', '.join(sorted(unknown))}",
            )
        current["weights"] = {**current["weights"], **supplied_weights}
    current.update(patch)
    if current["medium_threshold"] >= current["high_threshold"]:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="medium_threshold must be lower than high_threshold",
        )
    return save_settings(current)


@app.put("/api/v1/source/snapshot")
def put_snapshot(
    payload: SnapshotInput,
    _: str = Depends(require_user),
) -> dict[str, Any]:
    snapshot = payload.model_dump(mode="python")
    replace_snapshot(snapshot)
    return {
        "accounts": len(snapshot["accounts"]),
        "services": len(snapshot["services"]),
        "transactions": len(snapshot["transactions"]),
        "recalculated": True,
    }


@app.get("/metrics", include_in_schema=True)
def metrics() -> Response:
    customers, _settings = analytics()
    counts = Counter(item["risk_level"] for item in customers)
    lines = [
        "# HELP churn_customers Number of customers by risk level.",
        "# TYPE churn_customers gauge",
    ]
    for level in ("high", "medium", "low", "not_assessed"):
        lines.append(f'churn_customers{{risk_level="{level}"}} {counts.get(level, 0)}')
    lines.extend(
        [
            "# HELP churn_potential_loss_euro_cents Estimated potential revenue loss.",
            "# TYPE churn_potential_loss_euro_cents gauge",
            "churn_potential_loss_euro_cents "
            + str(sum(item["potential_loss"] for item in customers)),
            "# HELP churn_customer_risk_score Current customer risk score.",
            "# TYPE churn_customer_risk_score gauge",
        ]
    )
    for item in customers:
        if item["score"] is not None:
            lines.append(
                f'churn_customer_risk_score{{customer_id="{item["id"]}"}} {item["score"]}'
            )
    return Response("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")
