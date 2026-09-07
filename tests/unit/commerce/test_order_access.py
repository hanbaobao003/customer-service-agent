from datetime import UTC, datetime
from decimal import Decimal

import pytest

from customer_service_agent.commerce.orders import (
    Order,
    OrderNotFound,
    OrderService,
    OrderStatus,
)
from customer_service_agent.shared.models import RuntimeContext


NOW = datetime(2026, 8, 31, 8, 30, tzinfo=UTC)


def make_order(*, customer_id: str = "customer-a") -> Order:
    return Order(
        order_id="order-1",
        customer_id=customer_id,
        status=OrderStatus.PAID,
        contact_name="王小明",
        contact_phone="13812345678",
        shipping_address="上海市浦东新区示例路 100 号",
        currency="CNY",
        total_amount=Decimal("99.00"),
        items=(),
        created_at=NOW,
        updated_at=NOW,
        version=3,
    )


def context(customer_id: str) -> RuntimeContext:
    return RuntimeContext.trusted(
        customer_id=customer_id,
        thread_id="thread-1",
        request_id="request-1",
    )


class ScopedRepository:
    def __init__(self, order: Order | None) -> None:
        self.order = order
        self.calls: list[tuple[str, str]] = []

    async def get(self, order_id: str, customer_id: str) -> Order | None:
        self.calls.append((order_id, customer_id))
        if (
            self.order is not None
            and self.order.order_id == order_id
            and self.order.customer_id == customer_id
        ):
            return self.order
        return None


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("customer_id", ["customer-b", "customer-a"])
async def test_missing_and_other_customer_have_same_public_error(
    customer_id: str,
) -> None:
    stored = None if customer_id == "customer-a" else make_order()
    repo = ScopedRepository(stored)
    service = OrderService(repo=repo, clock=lambda: NOW)

    with pytest.raises(OrderNotFound) as exc:
        await service.get_order(context(customer_id), "order-1")

    assert exc.value.code == "ORDER_NOT_FOUND"
    assert repo.calls == [("order-1", customer_id)]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_order_read_contains_required_summary_and_masked_artifact() -> None:
    repo = ScopedRepository(make_order())
    service = OrderService(repo=repo, clock=lambda: NOW)

    result = await service.get_order(context("customer-a"), "order-1")

    assert result.content == {
        "order_id": "order-1",
        "status": "paid",
        "version": 3,
        "queried_at": "2026-08-31T08:30:00Z",
        "currency": "CNY",
        "total_amount": "99.00",
        "item_count": 0,
    }
    assert result.artifact["contact_phone"] == "*******5678"
    assert result.artifact["shipping_address"] == "上海市************"
    assert "13812345678" not in str(result.artifact)
    assert "上海市浦东新区示例路 100 号" not in str(result.artifact)
