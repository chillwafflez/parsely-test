from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings


def to_asyncpg_url(raw: str) -> str:
    """Adapt the Neon-provided connection string to asyncpg's expected form.

    Neon hands out `postgresql://user:pass@host/db?sslmode=require`. The
    asyncpg driver doesn't recognise the `sslmode` key (it uses `ssl`), and
    SQLAlchemy needs the `+asyncpg` driver suffix in the scheme.
    """
    url = raw
    if url.startswith("postgresql://"):
        url = "postgresql+asyncpg://" + url[len("postgresql://") :]
    return url.replace("sslmode=", "ssl=")


_settings = get_settings()

engine = create_async_engine(
    to_asyncpg_url(_settings.database_url),
    echo=False,
    pool_pre_ping=True,
)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
