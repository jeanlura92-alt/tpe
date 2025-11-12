import os
from sqlmodel import SQLModel, create_engine, Session
from sqlalchemy import text
from sqlalchemy.engine import Engine


def _normalize_database_url(url: str) -> str:
    """
    Normalise l'URL DB.
    - Si vide -> SQLite fichier local.
    - Convertit postgres:// -> postgresql+psycopg:// pour SQLAlchemy 2.x
    """
    url = (url or "").strip()
    if not url:
        return "sqlite:///./app.db"

    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg://", 1)
    elif url.startswith("postgresql://") and "+psycopg" not in url:
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


DATABASE_URL = _normalize_database_url(os.getenv("DATABASE_URL", ""))
engine = create_engine(DATABASE_URL, echo=False, future=True)


def get_session():
    with Session(engine) as session:
        yield session


def _column_exists(engine: Engine, table: str, column: str) -> bool:
    """
    Teste l'existence d'une colonne de manière portable.
    """
    driver = engine.url.get_backend_name()
    with engine.connect() as conn:
        if driver.startswith("postgresql"):
            sql = text("""
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = :t AND column_name = :c
                LIMIT 1
            """)
            res = conn.execute(sql, {"t": table, "c": column}).first()
            return bool(res)
        else:
            # SQLite
            res = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
            cols = {row[1] for row in res}  # row[1] = name
            return column in cols


def _add_column_if_missing(engine: Engine, table: str, column: str, ddl_sql_sqlite: str, ddl_sql_pg: str):
    """
    Ajoute une colonne si absente (SQLite/Postgres).
    - ddl_sql_* doivent être des 'ALTER TABLE ... ADD COLUMN ...' complets.
    """
    if _column_exists(engine, table, column):
        return
    driver = engine.url.get_backend_name()
    ddl = ddl_sql_pg if driver.startswith("postgresql") else ddl_sql_sqlite
    with engine.begin() as conn:
        conn.execute(text(ddl))


def _run_light_migrations(engine: Engine):
    """
    Petites migrations idempotentes pour aligner le schéma existant.
    """
    # CONTACT.TYPE
    _add_column_if_missing(
        engine,
        table="contact",
        column="type",
        ddl_sql_sqlite="ALTER TABLE contact ADD COLUMN type TEXT NOT NULL DEFAULT 'client';",
        ddl_sql_pg="ALTER TABLE contact ADD COLUMN IF NOT EXISTS type VARCHAR NOT NULL DEFAULT 'client';",
    )
    # CONTACT.EMAIL
    _add_column_if_missing(
        engine,
        table="contact",
        column="email",
        ddl_sql_sqlite="ALTER TABLE contact ADD COLUMN email TEXT;",
        ddl_sql_pg="ALTER TABLE contact ADD COLUMN IF NOT EXISTS email VARCHAR;",
    )
    # CONTACT.COMPANY
    _add_column_if_missing(
        engine,
        table="contact",
        column="company",
        ddl_sql_sqlite="ALTER TABLE contact ADD COLUMN company TEXT;",
        ddl_sql_pg="ALTER TABLE contact ADD COLUMN IF NOT EXISTS company VARCHAR;",
    )
    # CONTACT.ADDRESS
    _add_column_if_missing(
        engine,
        table="contact",
        column="address",
        ddl_sql_sqlite="ALTER TABLE contact ADD COLUMN address TEXT;",
        ddl_sql_pg="ALTER TABLE contact ADD COLUMN IF NOT EXISTS address VARCHAR;",
    )
    # CONTACT.TAGS
    _add_column_if_missing(
        engine,
        table="contact",
        column="tags",
        ddl_sql_sqlite="ALTER TABLE contact ADD COLUMN tags TEXT;",
        ddl_sql_pg="ALTER TABLE contact ADD COLUMN IF NOT EXISTS tags VARCHAR;",
    )

    # DEAL.STATUS
    _add_column_if_missing(
        engine,
        table="deal",
        column="status",
        ddl_sql_sqlite="ALTER TABLE deal ADD COLUMN status TEXT NOT NULL DEFAULT 'new';",
        ddl_sql_pg="ALTER TABLE deal ADD COLUMN IF NOT EXISTS status VARCHAR NOT NULL DEFAULT 'new';",
    )
    # DEAL.AMOUNT_ESTIMATED
    _add_column_if_missing(
        engine,
        table="deal",
        column="amount_estimated",
        ddl_sql_sqlite="ALTER TABLE deal ADD COLUMN amount_estimated REAL;",
        ddl_sql_pg="ALTER TABLE deal ADD COLUMN IF NOT EXISTS amount_estimated DOUBLE PRECISION;",
    )
    # DEAL.LAST_MESSAGE_PREVIEW
    _add_column_if_missing(
        engine,
        table="deal",
        column="last_message_preview",
        ddl_sql_sqlite="ALTER TABLE deal ADD COLUMN last_message_preview TEXT;",
        ddl_sql_pg="ALTER TABLE deal ADD COLUMN IF NOT EXISTS last_message_preview TEXT;",
    )
    # DEAL.LAST_MESSAGE_CHANNEL
    _add_column_if_missing(
        engine,
        table="deal",
        column="last_message_channel",
        ddl_sql_sqlite="ALTER TABLE deal ADD COLUMN last_message_channel TEXT;",
        ddl_sql_pg="ALTER TABLE deal ADD COLUMN IF NOT EXISTS last_message_channel VARCHAR;",
    )
    # DEAL.LAST_MESSAGE_AT
    _add_column_if_missing(
        engine,
        table="deal",
        column="last_message_at",
        ddl_sql_sqlite="ALTER TABLE deal ADD COLUMN last_message_at TIMESTAMP;",
        ddl_sql_pg="ALTER TABLE deal ADD COLUMN IF NOT EXISTS last_message_at TIMESTAMP;",
    )

    # OPTIONNEL : s'assurer que type/status ont une valeur par défaut pour les anciennes lignes nulles
    with engine.begin() as conn:
        conn.execute(text("UPDATE contact SET type='client' WHERE type IS NULL;"))
        conn.execute(text("UPDATE deal SET status='new' WHERE status IS NULL;"))


def create_db_and_tables():
    """
    1) Crée les tables manquantes
    2) Applique les mini-migrations (ajouts de colonnes) si besoin
    """
    from . import models  # enregistre les modèles
    SQLModel.metadata.create_all(engine)
    _run_light_migrations(engine)
