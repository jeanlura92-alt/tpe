import os
from contextlib import contextmanager

from sqlmodel import SQLModel, create_engine, Session
from sqlalchemy import text, inspect

# --------- Utils ----------
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

# --------- DATABASE_URL ----------
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL manquant. Définis cette variable sur Render.")

# Normalisation pour Psycopg (compat Python 3.13 / SQLAlchemy 2.x)
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)

# Pooling raisonnable pour Render
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

# --------- Mini-migrations idempotentes ----------
def _alter_table_add_columns_if_needed(table: str, column_defs: list[str]):
    """
    column_defs: liste de fragments SQL 'ADD COLUMN IF NOT EXISTS ...'
    """
    for frag in column_defs:
        stmt = f'ALTER TABLE "{table}" {frag};'
        with engine.begin() as conn:
            conn.execute(text(stmt))

def _run_migrations():
    """
    Effectue des migrations très simples et sûres :
      - s'assure que les tables existent
      - ajoute des colonnes si elles sont manquantes
    """
    # 1) Création des tables déclarées dans les modèles
    SQLModel.metadata.create_all(engine)

    insp = inspect(engine)
    tables = set(insp.get_table_names())

    # 2) CONTACT: s'assurer que 'type' existe
    if "contact" in tables:
        cols = {c["name"] for c in insp.get_columns("contact")}
        if "type" not in cols:
            _alter_table_add_columns_if_needed("contact", [
                'ADD COLUMN IF NOT EXISTS "type" TEXT DEFAULT \'client\''
            ])

    # 3) DEAL: colonnes méta de messages
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

    # 4) MESSAGE: table et colonnes (channel / direction / content / created_at)
    if "message" not in tables:
        # Si la table n'existe pas (nouvelle base), la créer via metadata
        SQLModel.metadata.create_all(engine)
    else:
        cols = {c["name"] for c in insp.get_columns("message")}
        adds = []
        if "direction" not in cols:
            adds.append('ADD COLUMN IF NOT EXISTS "direction" TEXT')
        if "channel" not in cols:
            adds.append('ADD COLUMN IF NOT EXISTS "channel" TEXT')
        if "content" not in cols:
            adds.append('ADD COLUMN IF NOT EXISTS "content" TEXT NOT NULL DEFAULT \'\'')
        if "created_at" not in cols:
            adds.append('ADD COLUMN IF NOT EXISTS "created_at" TIMESTAMPTZ DEFAULT NOW()')
        if "deal_id" not in cols:
            adds.append('ADD COLUMN IF NOT EXISTS "deal_id" INTEGER')
        if adds:
            _alter_table_add_columns_if_needed("message", adds)

def create_db_and_tables():
    try:
        _run_migrations()
    except Exception as e:
        # Log léger sans exposer le mot de passe
        safe_url = _mask_url(DATABASE_URL)
        print(f"[DB] Erreur migrations sur {safe_url}: {e}")
        # On tente au minimum la création brute
        SQLModel.metadata.create_all(engine)
