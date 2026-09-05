"""Owned resource names for local MVP seed data."""

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
import asyncio
import os
from pathlib import Path

import psycopg
from langchain_core.embeddings import Embeddings
from neo4j import GraphDatabase
from pymilvus import MilvusClient

from customer_service_agent.commerce.postgres import PostgresCommerceStore
from customer_service_agent.memory.mem0_pg import Mem0PgConfig, Mem0PgMemoryStore
from customer_service_agent.memory.service import (
    MemoryKind,
    MemoryRecord,
    MemorySource,
    MemorySourceType,
)
from customer_service_agent.mvp.settings import validate_owned_resource
from customer_service_agent.mvp.settings import MvpSettings
from customer_service_agent.retrieval import hybrid, raptor


_MVP_PREFIX = "wang_agent_mvp_"


@dataclass(frozen=True)
class SeedReport:
    orders: tuple[str, ...]
    faq_collection: str = ""
    raptor_collection: str = ""
    graph_version: str = ""
    memory_customer_id: str = ""
    memory_count: int = 0


@dataclass(frozen=True)
class MvpResources:
    faq_collection: str
    raptor_collection: str
    graph_version: str

    def __post_init__(self) -> None:
        for name in (self.faq_collection, self.raptor_collection, self.graph_version):
            validate_owned_resource(name, _MVP_PREFIX)

    @classmethod
    def default(cls) -> "MvpResources":
        return cls(
            faq_collection="wang_agent_mvp_faq_v1",
            raptor_collection="wang_agent_mvp_raptor_v1",
            graph_version="wang_agent_mvp_v1",
        )


async def seed_postgres(settings: object) -> SeedReport:
    postgres_dsn = getattr(settings, "postgres_dsn")
    store = PostgresCommerceStore(postgres_dsn)
    await store.setup()
    now = datetime.now(UTC)
    async with await psycopg.AsyncConnection.connect(postgres_dsn) as connection:
        for product in _PRODUCTS:
            await connection.execute(
                """
                INSERT INTO catalog_products_data (
                    product_id, product_name, unit_price, currency, active
                ) VALUES (%s, %s, %s, %s, true)
                ON CONFLICT (product_id) DO NOTHING
                """,
                product,
            )
        for order in _ORDERS:
            await connection.execute(
                """
                INSERT INTO orders (
                    order_id, customer_id, status, contact_name, contact_phone,
                    shipping_address, currency, total_amount, created_at,
                    updated_at, version, cancellation_refund_status
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1, NULL)
                ON CONFLICT (order_id) DO NOTHING
                """,
                (*order, now, now),
            )
        for item in _ORDER_ITEMS:
            await connection.execute(
                """
                INSERT INTO order_items (
                    order_id, position, product_id, product_name_snapshot,
                    unit_price, quantity
                ) VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (order_id, position) DO NOTHING
                """,
                item,
            )
    return SeedReport(orders=tuple(item[0] for item in _ORDERS))


async def seed_milvus(settings: object) -> SeedReport:
    resources = MvpResources.default()
    client = MilvusClient(uri=getattr(settings, "milvus_uri"))
    if not client.has_collection(resources.faq_collection):
        hybrid.create_hybrid_collection(client, resources.faq_collection)
        hybrid.MilvusHybridIndex(
            client=client,
            collection_name=resources.faq_collection,
            data_version=resources.faq_collection,
            embed_query=_demo_vector,
        ).insert(_FAQ_DOCUMENTS)
    if not client.has_collection(resources.raptor_collection):
        raptor.create_raptor_collection(client, resources.raptor_collection)
        nodes = _raptor_nodes()
        raptor.MilvusRaptorStore(
            client=client,
            collection_name=resources.raptor_collection,
            data_version=resources.raptor_collection,
            embed_query=_demo_vector,
        ).insert(
            nodes=nodes,
            dense_vectors={item.node_id: _demo_vector(item.text) for item in nodes},
        )
    return SeedReport(
        orders=(),
        faq_collection=resources.faq_collection,
        raptor_collection=resources.raptor_collection,
    )


def seed_neo4j(settings: object) -> SeedReport:
    resources = MvpResources.default()
    driver = GraphDatabase.driver(
        getattr(settings, "neo4j_uri"),
        auth=(
            getattr(settings, "neo4j_username"),
            getattr(settings, "neo4j_password"),
        ),
    )
    try:
        driver.execute_query(
            """
            MERGE (headphones:Product {
                product_id: 'MVP-PRODUCT-1001', data_version: $data_version
            })
            SET headphones.name = '云端降噪耳机', headphones.active = true
            MERGE (bag:Product {
                product_id: 'MVP-PRODUCT-1002', data_version: $data_version
            })
            SET bag.name = '旅行收纳包', bag.active = true
            MERGE (brand:Brand {
                brand_id: 'MVP-BRAND-100', data_version: $data_version
            })
            SET brand.name = '云澜'
            MERGE (category:Category {
                category_id: 'MVP-CATEGORY-AUDIO', data_version: $data_version
            })
            SET category.name = '音频设备'
            MERGE (attribute:Attribute {
                attribute_id: 'MVP-ATTRIBUTE-WARRANTY', data_version: $data_version
            })
            SET attribute.name = '保修期', attribute.value = '12 个月'
            MERGE (promotion:Promotion {
                promotion_id: 'MVP-PROMOTION-100', data_version: $data_version
            })
            SET promotion.name = '演示满减活动',
                promotion.starts_at = datetime('2026-01-01T00:00:00Z'),
                promotion.ends_at = datetime('2026-12-31T23:59:59Z'),
                promotion.rules = '满 399 元减 20 元'
            MERGE (headphones)-[:MADE_BY]->(brand)
            MERGE (headphones)-[:IN_CATEGORY]->(category)
            MERGE (headphones)-[:HAS_ATTRIBUTE]->(attribute)
            MERGE (headphones)-[:ELIGIBLE_FOR]->(promotion)
            MERGE (headphones)-[:RELATED_TO]->(bag)
            """,
            parameters_={"data_version": resources.graph_version},
            database_="neo4j",
        )
    finally:
        driver.close()
    return SeedReport(orders=(), graph_version=resources.graph_version)


async def seed_mem0(
    settings: object,
    *,
    history_db_path: str | None = None,
) -> SeedReport:
    path = Path(history_db_path or ".mvp-runtime/mem0-history.db")
    _configure_mem0_runtime(path)
    from mem0 import AsyncMemory

    config = Mem0PgConfig(
        dbname="wang_agent_mvp_mem0",
        connection_string_ref="MVP_MEM0_DSN",
    )
    mem0_config = config.to_mem0_config(
        connection_string=getattr(settings, "mem0_dsn"),
        embedder_config={
            "provider": "langchain",
            "config": {"model": _DeterministicEmbeddings()},
        },
        history_db_path=str(path),
    )
    mem0_config["llm"] = {
        "provider": "openai",
        "config": {
            "api_key": "not-used-with-infer-false",
            "model": "not-called-with-infer-false",
            "openai_base_url": "http://127.0.0.1:9/v1",
        },
    }
    memory = AsyncMemory.from_config(mem0_config)
    store = Mem0PgMemoryStore(memory)
    try:
        records = await store.list(customer_id="demo-customer-a", category="language")
        if not any(item.content == "偏好简洁中文回答" for item in records):
            now = datetime.now(UTC)
            await store.add(
                customer_id="demo-customer-a",
                record=MemoryRecord(
                    memory_id="mvp-seed-language-preference",
                    customer_id="demo-customer-a",
                    kind=MemoryKind.PREFERENCE,
                    content="偏好简洁中文回答",
                    source=MemorySource(
                        type=MemorySourceType.EXPLICIT_USER_INSTRUCTION,
                        thread_id="mvp-seed-thread",
                        request_id="mvp-seed-request",
                    ),
                    created_at=now,
                    updated_at=now,
                    category="language",
                ),
            )
        records = await store.list(customer_id="demo-customer-a", category="language")
        return SeedReport(
            orders=(),
            memory_customer_id="demo-customer-a",
            memory_count=len(records),
        )
    finally:
        memory.vector_store.connection_pool.close()


def _configure_mem0_runtime(history_db_path: Path) -> None:
    history_db_path.parent.mkdir(parents=True, exist_ok=True)
    os.environ["MEM0_TELEMETRY"] = "false"
    os.environ["MEM0_DIR"] = str(history_db_path.parent / "mem0-runtime")


async def seed_mvp(
    settings: object,
    *,
    history_db_path: str | None = None,
) -> SeedReport:
    postgres_report = await seed_postgres(settings)
    milvus_report = await seed_milvus(settings)
    graph_report = seed_neo4j(settings)
    memory_report = await seed_mem0(settings, history_db_path=history_db_path)
    return SeedReport(
        orders=postgres_report.orders,
        faq_collection=milvus_report.faq_collection,
        raptor_collection=milvus_report.raptor_collection,
        graph_version=graph_report.graph_version,
        memory_customer_id=memory_report.memory_customer_id,
        memory_count=memory_report.memory_count,
    )


_PRODUCTS = (
    ("MVP-PRODUCT-1001", "云端降噪耳机", "399.00", "CNY"),
    ("MVP-PRODUCT-1002", "旅行收纳包", "99.00", "CNY"),
    ("MVP-PRODUCT-1003", "Type-C 快充线", "49.00", "CNY"),
)
_ORDERS = (
    (
        "MVP-ORDER-1001",
        "demo-customer-a",
        "paid",
        "王小明",
        "13800000001",
        "北京市朝阳区演示路 1 号",
        "CNY",
        "399.00",
    ),
    (
        "MVP-ORDER-2001",
        "demo-customer-b",
        "delivered",
        "李小红",
        "13800000002",
        "上海市浦东新区演示路 2 号",
        "CNY",
        "99.00",
    ),
)
_ORDER_ITEMS = (
    ("MVP-ORDER-1001", 1, "MVP-PRODUCT-1001", "云端降噪耳机", "399.00", 1),
    ("MVP-ORDER-2001", 1, "MVP-PRODUCT-1002", "旅行收纳包", "99.00", 1),
)


def _demo_vector(text: str) -> tuple[float, ...]:
    digest = sha256(text.encode("utf-8")).digest()
    return tuple(((digest[index % len(digest)] / 255) * 2) - 1 for index in range(1024))


class _DeterministicEmbeddings(Embeddings):
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_query(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return list(_demo_vector(text))


async def _main() -> None:
    report = await seed_mvp(MvpSettings.from_environment(os.environ))
    print(
        "MVP demo data ready: "
        f"orders={','.join(report.orders)} "
        f"faq={report.faq_collection} "
        f"raptor={report.raptor_collection} "
        f"graph={report.graph_version} "
        f"memories={report.memory_count}"
    )


if __name__ == "__main__":
    asyncio.run(_main())


_FAQ_DOCUMENTS = (
    hybrid.HybridIndexDocument(
        child_id="mvp-faq-warranty-child",
        parent_id="mvp-faq-warranty-parent",
        source_id="mvp-faq-warranty",
        title="耳机保修说明",
        child_text="云端降噪耳机提供 12 个月有限保修。",
        parent_text="云端降噪耳机自签收日起提供 12 个月有限保修；人为损坏不在保修范围内。",
        child_locator="warranty-1",
        parent_locator="warranty",
        data_version="wang_agent_mvp_faq_v1",
        dense_vector=_demo_vector("云端降噪耳机提供 12 个月有限保修。"),
    ),
    hybrid.HybridIndexDocument(
        child_id="mvp-faq-delivery-child",
        parent_id="mvp-faq-delivery-parent",
        source_id="mvp-faq-delivery",
        title="配送说明",
        child_text="订单满 99 元可享受普通快递包邮。",
        parent_text="订单满 99 元可享受普通快递包邮；偏远地区和加急服务按页面结算信息为准。",
        child_locator="delivery-1",
        parent_locator="delivery",
        data_version="wang_agent_mvp_faq_v1",
        dense_vector=_demo_vector("订单满 99 元可享受普通快递包邮。"),
    ),
)


def _raptor_nodes() -> tuple[raptor.RaptorNode, ...]:
    leaf = raptor.RaptorNode.leaf(
        document_id="mvp-return-policy",
        text="已付款且未发货的订单可以申请取消，退款原路返回。",
        source_id="mvp-policy-return",
        locator="section-cancel",
        data_version="wang_agent_mvp_raptor_v1",
    )
    level_one = raptor.RaptorNode.summary(
        document_id=leaf.document_id,
        level=1,
        text="取消订单规则摘要。",
        child_ids=(leaf.node_id,),
        source_ids=leaf.source_ids,
        data_version=leaf.data_version,
    )
    level_two = raptor.RaptorNode.summary(
        document_id=leaf.document_id,
        level=2,
        text="退款与取消政策摘要。",
        child_ids=(level_one.node_id,),
        source_ids=leaf.source_ids,
        data_version=leaf.data_version,
    )
    level_three = raptor.RaptorNode.summary(
        document_id=leaf.document_id,
        level=3,
        text="售后政策总览。",
        child_ids=(level_two.node_id,),
        source_ids=leaf.source_ids,
        data_version=leaf.data_version,
    )
    return leaf, level_one, level_two, level_three
