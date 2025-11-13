import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from .database import create_db_and_tables, get_session
from .models import Contact, Deal, Message

# -----------------------------------------------------------------------------
# App & static / templates
# -----------------------------------------------------------------------------
app = FastAPI()
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")  # <= UTILISER ÇA, pas app.state

# -----------------------------------------------------------------------------
# Kanban columns (clé 'label' utilisée par dashboard.html)
# -----------------------------------------------------------------------------
COLUMNS = [
    {"id": "new",         "label": "Nouveau"},
    {"id": "in_progress", "label": "En cours"},
    {"id": "waiting",     "label": "En attente"},
    {"id": "done",        "label": "Terminé"},
]
COLUMN_IDS = {c["id"] for c in COLUMNS}

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
    msgs_before: Optional[str] = None,   # placeholders si tu ajoutes la pagination
    msgs_limit: Optional[int] = None,
    session: Session = Depends(get_session),
):
    try:
        # 1) Tous les contacts pour le panneau droit (toujours affichés)
        all_contacts = session.exec(
            select(Contact).order_by(Contact.name)
        ).all()

        # 2) Profil courant pour afficher/masquer le Kanban
        current_profile = profile if profile in ["client", "prospect", "fournisseur", "autre"] else None

        # 3) Deals par statut (uniquement si profil sélectionné)
        deals_by_status = {c["id"]: [] for c in COLUMNS}
        if current_profile:
            rows = session.exec(
                select(Deal, Contact)
                .join(Contact, Deal.contact_id == Contact.id)
                .where(Contact.type == current_profile)
            ).all()
            for deal, contact in rows:
                deals_by_status[deal.status].append({"deal": deal, "contact": contact})

        # 4) Fil WhatsApp (si un contact est sélectionné)
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
            "contacts": all_contacts,              # <= IMPORTANT pour la liste à droite
            "current_profile": current_profile,
            "deals_by_status": deals_by_status,
            "columns": COLUMNS,
            "selected": selected,
            "messages": messages,
            # valeurs pour UI (facultatives)
            "messages_next_cursor": None,
            "messages_limit": msgs_limit or 50,
            "total_contacts": len(all_contacts),
            "total_deals": sum(len(v) for v in deals_by_status.values())
        })
    finally:
        # Sécurise la fermeture pour éviter les timeouts de pool
        session.close()

# -----------------------------------------------------------------------------
# Maj contact
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
# Drag & Drop : changer le statut d'un deal
# -----------------------------------------------------------------------------
@app.post("/deals/{deal_id}/status")
def update_deal_status(
    deal_id: int,
    status: str = Form(...),   # <= correspond EXACTEMENT au FormData 'status' envoyé par le JS
    session: Session = Depends(get_session),
):
    try:
        if status not in COLUMN_IDS:
            raise HTTPException(400, "Statut invalide")

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
    content: str = Form(...),  # <= correspond EXACTEMENT au <textarea name="content">
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
            sent_at=now,  # si ta colonne est NOT NULL, on l'alimente
        )
        session.add(msg)

        # Hydrate le résumé sur le deal (utile pour le Kanban)
        deal.last_message_preview = (content or "")[:120]
        deal.last_message_channel = "WhatsApp"
        deal.last_message_at = now
        session.add(deal)

        session.commit()
        # (Ici tu peux déclencher l'appel API Meta si nécessaire)

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
        # Exemple d'extraction basique (à adapter selon la payload réelle)
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
                        # maj résumé deal
                        deal.last_message_preview = (text or "")[:120]
                        deal.last_message_channel = "WhatsApp"
                        deal.last_message_at = now
                        session.add(deal)

                        session.commit()
        return JSONResponse({"status": "received"})
    finally:
        session.close()
