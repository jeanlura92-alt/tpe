import os
from datetime import datetime, timezone
from typing import Optional, List, Dict, Set

from fastapi import FastAPI, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from .database import create_db_and_tables, get_session
from .models import Contact, Deal, Message

# -----------------------------------------------------------------------------
# App / static / templates
# -----------------------------------------------------------------------------
app = FastAPI()
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

# -----------------------------------------------------------------------------
# Pipelines par profil
# (ajuste les étiquettes/ids si tu veux d'autres colonnes)
# -----------------------------------------------------------------------------
PIPELINES: Dict[str, List[Dict[str, str]]] = {
    "client": [
        {"id": "new",         "label": "Nouveau"},
        {"id": "in_progress", "label": "En cours"},
        {"id": "waiting",     "label": "En attente"},
        {"id": "done",        "label": "Terminé"},
    ],
    "prospect": [
        {"id": "new",      "label": "Nouveau"},
        {"id": "quote",    "label": "Devis envoyé"},
        {"id": "followup", "label": "Relance"},
        {"id": "won",      "label": "Gagné"},
        {"id": "lost",     "label": "Perdu"},
    ],
    "fournisseur": [
        {"id": "new",     "label": "Nouveau"},
        {"id": "rfq",     "label": "Demande"},
        {"id": "ordered", "label": "Commandé"},
        {"id": "received","label": "Reçu"},
    ],
    "autre": [
        {"id": "new",         "label": "Nouveau"},
        {"id": "in_progress", "label": "En cours"},
        {"id": "done",        "label": "Terminé"},
    ],
}

# Ensemble de TOUS les statuts autorisés (pour sécuriser l’API)
ALL_STATUS_IDS: Set[str] = {c["id"] for cols in PIPELINES.values() for c in cols}

def get_columns(profile: Optional[str]) -> List[Dict[str, str]]:
    if profile and profile in PIPELINES:
        return PIPELINES[profile]
    return []  # pas de Kanban si profil non choisi

# -----------------------------------------------------------------------------
# Startup
# -----------------------------------------------------------------------------
@app.on_event("startup")
def on_startup():
    create_db_and_tables()

# -----------------------------------------------------------------------------
# Dashboard
# -----------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    contact_id: Optional[int] = None,
    profile: Optional[str] = None,
    msgs_before: Optional[str] = None,
    msgs_limit: Optional[int] = None,
    session: Session = Depends(get_session),
):
    try:
        # Tous les contacts pour le panneau droit
        all_contacts = session.exec(select(Contact).order_by(Contact.name)).all()

        current_profile = profile if profile in PIPELINES else None
        columns = get_columns(current_profile)

        # Prépare les colonnes du Kanban si un profil est choisi
        deals_by_status: Dict[str, List[Dict[str, object]]] = {c["id"]: [] for c in columns}
        valid_status_ids = set(deals_by_status.keys())
        default_status = columns[0]["id"] if columns else None

        if current_profile:
            rows = session.exec(
                select(Deal, Contact)
                .join(Contact, Deal.contact_id == Contact.id)
                .where(Contact.type == current_profile)
            ).all()
            for deal, contact in rows:
                status_key = deal.status if deal.status in valid_status_ids else default_status
                # si aucune colonne (shouldn't happen) on skip
                if status_key is None:
                    continue
                deals_by_status[status_key].append({"deal": deal, "contact": contact})

        # Fil WhatsApp si un contact est sélectionné
        selected = None
        messages = []
        if contact_id:
            c = session.get(Contact, contact_id)
            if c:
                d = session.exec(select(Deal).where(Deal.contact_id == c.id)).first()
                if d:
                    selected = {"contact": c, "deal": d}
                    messages = session.exec(
                        select(Message)
                        .where(Message.deal_id == d.id)
                        .order_by(Message.created_at.asc())
                    ).all()

        return templates.TemplateResponse("dashboard.html", {
            "request": request,
            "contacts": all_contacts,
            "current_profile": current_profile,
            "deals_by_status": deals_by_status,
            "columns": columns,
            "selected": selected,
            "messages": messages,
            "messages_next_cursor": None,
            "messages_limit": msgs_limit or 50,
            "total_contacts": len(all_contacts),
            "total_deals": sum(len(v) for v in deals_by_status.values()) if current_profile else 0,
        })
    finally:
        session.close()

# -----------------------------------------------------------------------------
# Mise à jour d’un contact
# -----------------------------------------------------------------------------
@app.post("/contacts/{contact_id}/update")
def update_contact(
    contact_id: int,
    name: str = Form(...),
    phone: str = Form(...),
    contact_type: str = Form(...),
    email: Optional[str] = Form(None),
    company: Optional[str] = Form(None),
    address: Optional[str] = Form(None),
    tags: Optional[str] = Form(None),
    session: Session = Depends(get_session),
):
    try:
        c = session.get(Contact, contact_id)
        if not c:
            raise HTTPException(404, "Contact introuvable")

        c.name = name
        c.phone = phone
        c.type = contact_type
        c.email = email
        c.company = company
        c.address = address
        c.tags = tags
        session.add(c)
        session.commit()
        return JSONResponse({"ok": True})
    finally:
        session.close()

# -----------------------------------------------------------------------------
# Drag & Drop : changer le statut d’un deal
# -----------------------------------------------------------------------------
@app.post("/deals/{deal_id}/status")
def update_deal_status(
    deal_id: int,
    status: str = Form(...),  # FormData('status') envoyé par le JS
    session: Session = Depends(get_session),
):
    try:
        if status not in ALL_STATUS_IDS:
            raise HTTPException(400, f"Statut invalide: {status}")

        deal = session.get(Deal, deal_id)
        if not deal:
            raise HTTPException(404, "Deal introuvable")

        deal.status = status
        session.add(deal)
        session.commit()
        return JSONResponse({"ok": True, "deal_id": deal_id, "new_status": status})
    finally:
        session.close()

# -----------------------------------------------------------------------------
# Envoi message WhatsApp (sortant)
# -----------------------------------------------------------------------------
@app.post("/deals/{deal_id}/send_message")
def send_whatsapp_message(
    deal_id: int,
    content: str = Form(...),  # <textarea name="content">
    session: Session = Depends(get_session),
):
    try:
        deal = session.get(Deal, deal_id)
        if not deal:
            raise HTTPException(404, "Deal introuvable")

        contact = session.get(Contact, deal.contact_id)
        if not contact:
            raise HTTPException(404, "Contact introuvable")

        now = datetime.now(timezone.utc)
        msg = Message(
            deal_id=deal.id,
            contact_id=contact.id,
            direction="out",
            channel="WhatsApp",
            content=content,
            created_at=now,
            sent_at=now,  # si colonne NOT NULL
        )
        session.add(msg)

        # Mettre à jour le résumé sur le Deal pour le Kanban
        deal.last_message_preview = (content or "")[:120]
        deal.last_message_channel = "WhatsApp"
        deal.last_message_at = now
        session.add(deal)

        session.commit()
        return JSONResponse({"ok": True})
    finally:
        session.close()

# -----------------------------------------------------------------------------
# Webhook WhatsApp (entrant)
# -----------------------------------------------------------------------------
@app.post("/webhook")
async def receive_webhook(request: Request, session: Session = Depends(get_session)):
    try:
        data = await request.json()
        if "entry" in data:
            for entry in data["entry"]:
                for change in entry.get("changes", []):
                    value = change.get("value", {})
                    for m in value.get("messages", []):
                        phone = m.get("from")
                        text = m.get("text", {}).get("body", "")
                        if not phone or text is None:
                            continue

                        contact = session.exec(
                            select(Contact).where(Contact.phone == phone)
                        ).first()
                        if not contact:
                            continue

                        deal = session.exec(
                            select(Deal).where(Deal.contact_id == contact.id)
                        ).first()
                        if not deal:
                            continue

                        now = datetime.now(timezone.utc)
                        session.add(Message(
                            deal_id=deal.id,
                            contact_id=contact.id,
                            direction="in",
                            channel="WhatsApp",
                            content=text,
                            created_at=now,
                            sent_at=now,
                        ))
                        deal.last_message_preview = (text or "")[:120]
                        deal.last_message_channel = "WhatsApp"
                        deal.last_message_at = now
                        session.add(deal)

                        session.commit()
        return JSONResponse({"status": "received"})
    finally:
        session.close()