import os
from sqlmodel import SQLModel, create_engine, Session
from contextlib import contextmanager


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


# 1) DATABASE_URL obligatoire
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL manquant. Définis cette variable sur Render.")

# 2) Normalisation d'URL ➜ forcer le dialecte psycopg (psycopg3)
#    - postgres://...                 ➜ postgresql+psycopg://...
#    - postgresql://...               ➜ postgresql+psycopg://...
#    - postgresql+psycopg2://...      ➜ postgresql+psycopg://...
#    - postgresql+psycopg://...       (déjà OK)
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql+psycopg2://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql+psycopg2://", "postgresql+psycopg://", 1)

# 3) Pooling Render (petit dyno)
POOL_SIZE = int(os.getenv("SQL_POOL_SIZE", "5"))
MAX_OVERFLOW = int(os.getenv("SQL_MAX_OVERFLOW", "5"))
POOL_TIMEOUT = int(os.getenv("SQL_POOL_TIMEOUT", "20"))
POOL_RECYCLE = int(os.getenv("SQL_POOL_RECYCLE", "1800"))

engine = create_engine(
    DATABASE_URL,
    echo=os.getenv("SQL_ECHO", "0") == "1",
    pool_size=POOL_SIZE,
    max_overflow=MAX_OVERFLOW,
    pool_timeout=POOL_TIMEOUT,
    pool_recycle=POOL_RECYCLE,
    pool_pre_ping=True,
    future=True,
)


def create_db_and_tables() -> None:
    SQLModel.metadata.create_all(engine)


@contextmanager
def session_scope():
    s = Session(engine)
    try:
        yield s
    finally:
        try:
            s.close()
        except Exception:
            pass


def get_session():
    s = Session(engine)
    try:
        yield s
    finally:
        try:
            s.close()
        except Exception:
            pass
