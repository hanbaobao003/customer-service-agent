from datetime import UTC, datetime
from decimal import Decimal

import pytest

from customer_service_agent.commerce.orders import (
    BusinessRuleRejected,
    Order,
    OrderStatus,
    transition,
)


def make_order(*, status: OrderStatus, version: int = 1) -> Order:
    now = datetime(2026, 8, 31, tzinfo=UTC)
    return Order(
        order_id="order-1",
        customer_id="customer-1",
        status=status,
        contact_name="王小明",
        contact_phone="13800000000",
        shipping_address="上海市示例路 1 号",
        currency="CNY",
        total_amount=Decimal("99.00"),
        items=(),
        created_at=now,
        updated_at=now,
        version=version,
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("source", "target"),
    [
        (OrderStatus.PENDING_PAYMENT, OrderStatus.PAID),
        (OrderStatus.PAID, OrderStatus.PROCESSING),
        (OrderStatus.PROCESSING, OrderStatus.SHIPPED),
        (OrderStatus.SHIPPED, OrderStatus.DELIVERED),
        (OrderStatus.DELIVERED, OrderStatus.RETURN_REQUESTED),
        (OrderStatus.PENDING_PAYMENT, OrderStatus.CANCELLED),
        (OrderStatus.PAID, OrderStatus.CANCELLED),
        (OrderStatus.PROCESSING, OrderStatus.CANCELLED),
    ],
)
def test_allowed_transition_returns_new_version(
    source: OrderStatus,
    target: OrderStatus,
) -> None:
    order = make_order(status=source, version=4)

    changed = transition(order, target)

    assert changed.status is target
    assert changed.version == 5
    assert order.status is source
    assert order.version == 4


@pytest.mark.unit
@pytest.mark.parametrize(
    ("source", "target"),
    [
        (OrderStatus.SHIPPED, OrderStatus.CANCELLED),
        (OrderStatus.DELIVERED, OrderStatus.CANCELLED),
        (OrderStatus.PAID, OrderStatus.DELIVERED),
        (OrderStatus.CANCELLED, OrderStatus.PAID),
        (OrderStatus.RETURN_REQUESTED, OrderStatus.RETURNED),
    ],
)
def test_forbidden_transition_is_rejected_without_mutation(
    source: OrderStatus,
    target: OrderStatus,
) -> None:
    order = make_order(status=source, version=4)

    with pytest.raises(BusinessRuleRejected) as exc:
        transition(order, target)

    assert exc.value.code == "BUSINESS_RULE_REJECTED"
    assert order.status is source
    assert order.version == 4
