"""Marks paid orders as shipped once the warehouse confirms them."""

import os

# Must match the Go constants in server/main.go: the API reads what we write.
STATUSES = {"pending", "paid", "shipped"}

DB_URL = os.environ["SHOP_DB_URL"]
POLL_SECONDS = int(os.getenv("SHOP_WORKER_POLL_SECONDS", "30"))


def ship(order_id: str, status: str) -> str:
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r}")
    return "shipped" if status == "paid" else status
