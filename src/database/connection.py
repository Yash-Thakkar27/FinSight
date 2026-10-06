"""Database engine. One engine per process, built from .env via config/settings.py."""

from functools import lru_cache

from sqlalchemy import Engine, create_engine

from config.settings import get_database_settings


@lru_cache
def get_engine() -> Engine:
    return create_engine(get_database_settings().url, pool_pre_ping=True)
