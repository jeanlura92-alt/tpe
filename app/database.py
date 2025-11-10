# app/database.py
import os
from sqlmodel import SQLModel, create_engine, Session


def _normalize_database_url(url: str) -> str:
    """
    Normalise l'URL Postgres (ex: postgres:// -> postgresql+psycopg://).
    Compatible Render / autres hébergeurs.
    """
    url = url.strip()
    if not url:
        return "sqlite:///./app.db"

    # Cas Render / Heroku
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


def create_db_and_tables():
    from . import models  # important pour que les tables soient connues
    SQLModel.metadata.create_all(engine)
