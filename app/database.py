import os
from contextlib import contextmanager
from sqlmodel import SQLModel, create_engine, Session
from sqlalchemy import text, inspect


def _mask_url(url: str) -> str:
    try:
        if "://" not in url:
            return url
        scheme, rest = url.split("://", 1)
        if "@" not in rest or ":" not in rest.split("@", 1)[0]:
            return url
        creds, hostpart = rest.split("@", 1)
        user, _pwd = creds.split(":", 1)
        return f"{scheme}://{user}:****@{hostpart}"
    except Exception:
        return url


DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL manquant. Définis cette variable sur Render.")

# forcer psycopg v3
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=int(os.getenv("DB_POOL_SIZE", "5")),
    max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "5")),
    echo=os.getenv("SQL_ECHO", "0") == "1",
)

@contextmanager
def session_scope():
    with Session(engine) as session:
        yield session

def get_session() -> Session:
    return Session(engine)


def _alter_table_add_columns_if_needed(table: str, column_defs: list[str]):
    for frag in column_defs:
        stmt = f'ALTER TABLE "{table}" {frag};'
        with engine.begin() as conn:
            conn.execute(text(stmt))


def _run_migrations():
    # crée toutes les tables déclarées
    SQLModel.metadata.create_all(engine)

    insp = inspect(engine)
    tables = set(insp.get_table_names())

    # CONTACT
    if "contact" in tables:
        cols = {c["name"] for c in insp.get_columns("contact")}
        if "type" not in cols:
            _alter_table_add_columns_if_needed("contact", [
                'ADD COLUMN IF NOT EXISTS "type" TEXT DEFAULT \'client\''
            ])

    # DEAL
    if "deal" in tables:
        cols = {c["name"] for c in insp.get_columns("deal")}
        adds = []
        if "last_message_preview" not in cols:
            adds.append('ADD COLUMN IF NOT EXISTS "last_message_preview" TEXT')
        if "last_message_channel" not in cols:
            adds.append('ADD COLUMN IF NOT EXISTS "last_message_channel" TEXT')
        if "last_message_at" not in cols:
            adds.append('ADD COLUMN IF NOT EXISTS "last_message_at" TIMESTAMPTZ')
        if adds:
            _alter_table_add_columns_if_needed("deal", adds)

    # MESSAGE
    if "message" not in tables:
        SQLModel.metadata.create_all(engine)
    else:
        cols = {c["name"] for c in insp.get_columns("message")}
        adds = []
        if "deal_id" not in cols:
            adds.append('ADD COLUMN IF NOT EXISTS "deal_id" INTEGER')
        if "contact_id" not in cols:
            adds.append('ADD COLUMN IF NOT EXISTS "contact_id" INTEGER')
        if "direction" not in cols:
            adds.append('ADD COLUMN IF NOT EXISTS "direction" TEXT')
        if "channel" not in cols:
            adds.append('ADD COLUMN IF NOT EXISTS "channel" TEXT')
        if "content" not in cols:
            adds.append('ADD COLUMN IF NOT EXISTS "content" TEXT NOT NULL DEFAULT \'\'')
        if "created_at" not in cols:
            adds.append('ADD COLUMN IF NOT EXISTS "created_at" TIMESTAMPTZ DEFAULT NOW()')
        if "sent_at" not in cols:
            adds.append('ADD COLUMN IF NOT EXISTS "sent_at" TIMESTAMPTZ DEFAULT NOW()')  # <-- assure une valeur

        if adds:
            _alter_table_add_columns_if_needed("message", adds)

        # backfill sent_at & contact_id si null
        with engine.begin() as conn:
            # sent_at = created_at si absent
            conn.execute(text("""
                UPDATE "message" SET sent_at = COALESCE(sent_at, created_at, NOW())
                WHERE sent_at IS NULL
            """))
            # contact_id depuis deal
            conn.execute(text("""
                UPDATE "message" m
                SET contact_id = d.contact_id
                FROM "deal" d
                WHERE m.deal_id = d.id AND (m.contact_id IS NULL OR m.contact_id = 0)
            """))


def create_db_and_tables():
    try:
        _run_migrations()
    except Exception as e:
        safe_url = _mask_url(DATABASE_URL)
        print(f"[DB] Erreur migrations sur {safe_url}: {e}")
        SQLModel.metadata.create_all(engine)
