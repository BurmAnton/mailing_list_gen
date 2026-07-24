from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
engine = create_async_engine(
    settings.database_url,
    echo=False,
    connect_args={"timeout": 30},
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session


async def _ensure_sqlite_columns(conn) -> None:
    """Add new columns on existing SQLite DBs without Alembic."""
    if not str(engine.url).startswith("sqlite"):
        return

    # campaigns
    result = await conn.execute(text("PRAGMA table_info(campaigns)"))
    existing = {row[1] for row in result.fetchall()}
    alterations: list[str] = []
    if "llm_provider" not in existing:
        alterations.append(
            "ALTER TABLE campaigns ADD COLUMN llm_provider VARCHAR(32) NOT NULL DEFAULT 'openrouter'"
        )
    if "llm_model" not in existing:
        alterations.append("ALTER TABLE campaigns ADD COLUMN llm_model VARCHAR(255)")
    if "use_web_search" not in existing:
        alterations.append(
            "ALTER TABLE campaigns ADD COLUMN use_web_search BOOLEAN NOT NULL DEFAULT 0"
        )
    if "rag_digest_text" not in existing:
        alterations.append("ALTER TABLE campaigns ADD COLUMN rag_digest_text TEXT")
    if "rag_digest_hash" not in existing:
        alterations.append("ALTER TABLE campaigns ADD COLUMN rag_digest_hash VARCHAR(64)")
    if "use_segment_templates" not in existing:
        alterations.append(
            "ALTER TABLE campaigns ADD COLUMN use_segment_templates BOOLEAN NOT NULL DEFAULT 0"
        )
    if "segment_field" not in existing:
        alterations.append("ALTER TABLE campaigns ADD COLUMN segment_field VARCHAR(255)")

    # recipients
    result = await conn.execute(text("PRAGMA table_info(recipients)"))
    recip_cols = {row[1] for row in result.fetchall()}
    if "extra_fields_json" not in recip_cols:
        alterations.append("ALTER TABLE recipients ADD COLUMN extra_fields_json TEXT")

    # campaign_rag_files
    result = await conn.execute(text("PRAGMA table_info(campaign_rag_files)"))
    rag_cols = {row[1] for row in result.fetchall()}
    if rag_cols:
        if "source_type" not in rag_cols:
            alterations.append(
                "ALTER TABLE campaign_rag_files ADD COLUMN source_type VARCHAR(32) NOT NULL DEFAULT 'file'"
            )
        if "source_url" not in rag_cols:
            alterations.append("ALTER TABLE campaign_rag_files ADD COLUMN source_url VARCHAR(2048)")

    for stmt in alterations:
        await conn.execute(text(stmt))


async def init_db() -> None:
    from app import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _ensure_sqlite_columns(conn)
