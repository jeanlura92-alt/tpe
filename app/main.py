from datetime import datetime, timezone
import os
from typing import Dict, List, Optional, Tuple

from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .database import db


# ======================================================
# CONFIG SAAS / WORKSPACE
# ======================================================
DEFAULT_WORKSPACE_ID = os.getenv("DEFAULT_WORKSPACE_ID")
if not DEFAULT_WORKSPACE_ID:
    raise RuntimeError("DEFAULT_WORKSPACE_ID manquant (Render env var)")


# ======================================================
# App & Templating
# ======================================================
app = FastAPI()

BASE_DIR = os.path.dirname(__file__)
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")

env = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR),
    autoescape=select_autoescape(["html", "xml"])
)

if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def render_template(name: str, context: dict) -> HTMLResponse:
    template = env.get_template(name)
    return HTMLResponse(template.render(**context))


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


# ======================================================
# Constantes métier
# ======================================================
KANBAN_COLUMNS: List[Tuple[str, str]] = [
    ("new", "Nouveau"),
    ("to_do", "À traiter"),
    ("in_progress", "En cours"),
    ("won", "Gagné"),
    ("lost", "Perdu"),
]

PROFILE_ALLOWED = {"client", "prospect", "fournisseur", "autre"}


def current_profile_from_query(profile: Optional[str]) -> Optional[str]:
    return profile if profile in PROFILE_ALLOWED else None


# ======================================================
# Dashboard
# ======================================================
@app.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    contact_id: Optional[str] = None,
    profile: Optional[str] = None,
    msgs_limit: int = 30,
):
    current_profile = current_profile_from_query(profile)
    sb = db()

    # ---------- Contacts ----------
    q = (
        sb.table("contacts")
        .select("*")
        .eq("workspace_id", DEFAULT_WORKSPACE_ID)
        .order("created_at", desc=True)
    )
    if current_profile:
        q = q.eq("type", current_profile)

    all_contacts = q.execute().data or []

    # ---------- Kanban ----------
    deals_by_status: Dict[str, List[Dict]] = {k: [] for k, _ in KANBAN_COLUMNS}

    if current_profile:
        rows = (
            sb.table("deals")
            .select("*, contacts(*)")
            .eq("workspace_id", DEFAULT_WORKSPACE_ID)
            .eq("contacts.type", current_profile)
            .order("created_at", desc=True)
            .execute()
            .data
            or []
        )

        for row in rows:
            status = row.get("status", "new")
            bucket = status if status in deals_by_status else "new"
            deals_by_status[bucket].append({
                "deal": row,
                "contact": row.get("contacts")
            })

    # ---------- Contact sélectionné ----------
    selected = None
    messages = []

    if contact_id:
        contact = (
            sb.table("contacts")
            .select("*")
            .eq("id", contact_id)
            .eq("workspace_id", DEFAULT_WORKSPACE_ID)
            .single()
            .execute()
            .data
        )

        if not contact:
            raise HTTPException(404, "Contact introuvable")

        deal_resp = (
            sb.table("deals")
            .select("*")
            .eq("contact_id", contact_id)
            .eq("workspace_id", DEFAULT_WORKSPACE_ID)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
            .data
        )

        if not deal_resp:
            deal = (
                sb.table("deals")
                .insert({
                    "workspace_id": DEFAULT_WORKSPACE_ID,
                    "title": f"Deal {contact['name']}",
                    "status": "new",
                    "contact_id": contact_id,
                    "created_at": now_utc(),
                })
                .execute()
                .data[0]
            )
        else:
            deal = deal_resp[0]

        messages = (
            sb.table("messages")
            .select("*")
            .eq("workspace_id", DEFAULT_WORKSPACE_ID)
            .eq("deal_id", deal["id"])
            .order("created_at", desc=False)
            .limit(max(5, min(200, msgs_limit)))
            .execute()
            .data
            or []
        )

        selected = {"deal": deal, "contact": contact}

    total_contacts = len(all_contacts)
    total_deals = sum(len(v) for v in deals_by_status.values()) if current_profile else 0

    columns = [{"id": c[0], "label": c[1]} for c in KANBAN_COLUMNS]

    return render_template("dashboard.html", {
        "request": request,
        "columns": columns,
        "deals_by_status": deals_by_status,
        "current_profile": current_profile,
        "all_contacts": all_contacts,
        "contacts": all_contacts,
        "selected": selected,
        "messages": messages,
        "messages_limit": msgs_limit,
        "total_contacts": total_contacts,
        "total_deals": total_deals,
    })


# ======================================================
# Contacts
# ======================================================
@app.get("/contacts", response_class=HTMLResponse)
def contacts_page(request: Request):
    contacts = (
        db().table("contacts")
        .select("*")
        .eq("workspace_id", DEFAULT_WORKSPACE_ID)
        .order("created_at", desc=True)
        .execute()
        .data
        or []
    )
    return render_template("contacts.html", {"request": request, "contacts": contacts})


@app.get("/contacts/new", response_class=HTMLResponse)
def contacts_new_form(request: Request):
    return render_template("contact_form.html", {
        "request": request,
        "mode": "create",
        "error": None,
        "name": "",
        "phone": "",
        "email": "",
        "company": "",
        "address": "",
        "tags": "",
        "type": "client",
    })


@app.post("/contacts/new")
def contacts_create(
    request: Request,
    name: str = Form(...),
    phone: str = Form(...),
    email: str = Form(""),
    type: str = Form("client"),
    company: str = Form(""),
    address: str = Form(""),
    tags: str = Form(""),
):
    if not phone.strip():
        raise HTTPException(400, "Téléphone obligatoire")

    if type not in PROFILE_ALLOWED:
        type = "autre"

    sb = db()

    contact = (
        sb.table("contacts")
        .insert({
            "workspace_id": DEFAULT_WORKSPACE_ID,
            "name": name.strip(),
            "phone": phone.strip(),
            "email": email.strip() or None,
            "type": type,
            "company": company.strip() or None,
            "address": address.strip() or None,
            "tags": tags.strip() or None,
            "created_at": now_utc(),
        })
        .execute()
        .data[0]
    )

    sb.table("deals").insert({
        "workspace_id": DEFAULT_WORKSPACE_ID,
        "title": f"Deal {contact['name']}",
        "status": "new",
        "contact_id": contact["id"],
        "created_at": now_utc(),
    }).execute()

    return RedirectResponse(
        f"/?contact_id={contact['id']}&profile={contact['type']}",
        status_code=303
    )


# ======================================================
# Messages & Kanban
# ======================================================
@app.post("/deals/{deal_id}/send_message")
def send_whatsapp_message(deal_id: str, content: str = Form(...)):
    sb = db()

    deal = (
        sb.table("deals")
        .select("*")
        .eq("id", deal_id)
        .eq("workspace_id", DEFAULT_WORKSPACE_ID)
        .single()
        .execute()
        .data
    )
    if not deal:
        raise HTTPException(404, "Deal introuvable")

    contact = (
        sb.table("contacts")
        .select("*")
        .eq("id", deal["contact_id"])
        .eq("workspace_id", DEFAULT_WORKSPACE_ID)
        .single()
        .execute()
        .data
    )

    ts = now_utc()

    sb.table("messages").insert({
        "workspace_id": DEFAULT_WORKSPACE_ID,
        "deal_id": deal_id,
        "contact_id": contact["id"],
        "direction": "out",
        "channel": "WhatsApp",
        "content": content.strip(),
        "created_at": ts,
        "sent_at": ts,
    }).execute()

    sb.table("deals").update({
        "last_message_preview": content[:140],
        "last_message_channel": "WhatsApp",
        "last_message_at": ts,
    }).eq("id", deal_id).execute()

    return RedirectResponse(
        f"/?contact_id={contact['id']}&profile={contact['type']}",
        status_code=303
    )


@app.post("/deals/{deal_id}/status")
def update_deal_status(
    deal_id: str,
    status: str = Form(...),
    request: Request = None,
):
    if status not in {k for k, _ in KANBAN_COLUMNS}:
        raise HTTPException(400, "Statut invalide")

    db().table("deals").update({
        "status": status
    }).eq("id", deal_id).eq("workspace_id", DEFAULT_WORKSPACE_ID).execute()

    is_ajax = request and request.headers.get("X-Requested-With") == "XMLHttpRequest"
    if is_ajax:
        return JSONResponse({"ok": True, "deal_id": deal_id, "new_status": status})

    return RedirectResponse("/", status_code=303)


# ======================================================
# Healthcheck
# ======================================================
@app.head("/", response_class=PlainTextResponse)
def head_root():
    return PlainTextResponse("", status_code=200)
