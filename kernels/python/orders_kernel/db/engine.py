import logging
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, db_url: str) -> None:
        self._engine: AsyncEngine = create_async_engine(db_url, echo=False)
        self._session_factory = async_sessionmaker(
            bind=self._engine, expire_on_commit=False, autocommit=False, autoflush=False
        )

    async def connect(self):
        """Used for testing connection or initializing if needed."""
        pass

    async def disconnect(self):
        await self._engine.dispose()

    @asynccontextmanager
    async def session(self):
        session = self._session_factory()
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    async def get_session(self):
        """For FastAPI Depends"""
        async with self.session() as session:
            yield session
