import os
from contextlib import contextmanager
from supabase import create_client, Client


# ======================================================
# Utils
# ======================================================
def _mask_key(key: str) -> str:
    """
    Masque une clé sensible pour les logs
    """
    if not key or len(key) < 8:
        return "****"
    return key[:4] + "****" + key[-4:]


# ======================================================
# 1) VARIABLES D’ENV OBLIGATOIRES (SUPABASE)
# ======================================================
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()  # service_role côté backend

if not SUPABASE_URL:
    raise RuntimeError("SUPABASE_URL manquant. Définis cette variable sur Render.")

if not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_KEY manquant. Définis cette variable sur Render.")

# Debug léger si besoin (sans exposer la clé)
if os.getenv("DB_DEBUG", "0") == "1":
    print("[DB] SUPABASE_URL =", SUPABASE_URL)
    print("[DB] SUPABASE_KEY =", _mask_key(SUPABASE_KEY))


# ======================================================
# 2) CLIENT SUPABASE (singleton)
# ======================================================
_supabase: Client | None = None


def get_supabase() -> Client:
    """
    Retourne une instance unique du client Supabase
    (évite les re-créations inutiles)
    """
    global _supabase
    if _supabase is None:
        _supabase = create_client(
            SUPABASE_URL,
            SUPABASE_KEY
        )
    return _supabase


# ======================================================
# 3) API COMPATIBLE AVEC L’EXISTANT
# ======================================================
def db() -> Client:
    """
    Alias court utilisé partout dans l’app

    Exemple :
        db().table("contacts").select("*").execute()
    """
    return get_supabase()


# ======================================================
# 4) CONTEXT MANAGER (compatibilité mentale)
# ======================================================
@contextmanager
def session_scope():
    """
    Conservé volontairement pour ne pas casser
    la logique existante du code.

    Supabase n’a pas besoin de close(),
    mais ça permet une transition propre.
    """
    try:
        yield get_supabase()
    finally:
        pass


def get_session():
    """
    Generator conservé pour compatibilité FastAPI Depends
    """
    try:
        yield get_supabase()
    finally:
        pass
