import os
from datetime import datetime, timezone

from fastapi import FastAPI, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import SQLModel, Session, select
from jinja2 import TemplateNotFound

from .database import create_db_and_tables, get_session
from .models import Contact, Deal, Message

app = FastAPI()

app.mount("/static", StaticFiles(directory="app/static"), name="static")


# --------------------------------------------------------------------------
# Kanban columns configuration
# --------------------------------------------------------------------------
COLUMNS = [
    {"id": "new", "title": "Nouveau"},
    {"id": "in_progress", "title": "En cours"},
    {"id": "waiting", "title": "En attente"},
    {"id": "done", "title": "Terminé"}
]


# --------------------------------------------------------------------------
# Startup
# --------------------------------------------------------------------------
@app.on_event("startup")
def on_startup():
    create_db_and_tables()


# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    contact_id: int | None = None,
    profile: str | None = None,
    session: Session = Depends(get_session)
):
    try:
        # Charger TOUS LES CONTACTS pour le panneau droit
        all_contacts = session.exec(
            select(Contact).order_by(Contact.name)
        ).all()

        current_profile = profile if profile in ["client", "prospect", "fournisseur", "autre"] else None

        # Charger les deals si un profil est sélectionné
        deals_by_status = {c["id"]: [] for c in COLUMNS}
        if current_profile:
            rows = session.exec(
                select(Deal, Contact)
                .join(Contact, Deal.contact_id == Contact.id)
                .where(Contact.type == current_profile)
            ).all()

            for deal, contact in rows:
                deals_by_status[deal.status].append({"deal": deal, "contact": contact})

        # Thread si contact sélectionné
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
                        .order_by(Message.created_at)
                    ).all()

        return app.state.templates.TemplateResponse("dashboard.html", {
            "request": request,
            "contacts": all_contacts,                # IMPORTANT
            "current_profile": current_profile,
            "deals_by_status": deals_by_status,
            "columns": COLUMNS,
            "selected": selected,
            "messages": messages,
        })

    finally:
        session.close()


# --------------------------------------------------------------------------
# Update Contact
# --------------------------------------------------------------------------
@app.post("/contacts/{contact_id}/update")
def update_contact(
    contact_id: int,
    name: str = Form(...),
    phone: str = Form(...),
    contact_type: str = Form(...),
    session: Session = Depends(get_session)
):
    try:
        c = session.get(Contact, contact_id)
        if not c:
            raise HTTPException(404, "Contact introuvable")

        c.name = name
        c.phone = phone
        c.type = contact_type
        session.add(c)
        session.commit()

        return JSONResponse({"ok": True})

    finally:
        session.close()


# --------------------------------------------------------------------------
# Drag & Drop : Update deal status
# --------------------------------------------------------------------------
@app.post("/deals/{deal_id}/status")
def update_deal_status(
    deal_id: int,
    new_status: str = Form(...),
    session: Session = Depends(get_session)
):
    try:
        if new_status not in [c["id"] for c in COLUMNS]:
            raise HTTPException(400, "Statut invalide")

        deal = session.get(Deal, deal_id)
        if not deal:
            raise HTTPException(404, "Deal introuvable")

        deal.status = new_status
        session.add(deal)
        session.commit()

        return JSONResponse({"ok": True})

    finally:
        session.close()


# --------------------------------------------------------------------------
# Send WhatsApp message
# --------------------------------------------------------------------------
@app.post("/deals/{deal_id}/send_message")
def send_whatsapp_message(
    deal_id: int,
    message: str = Form(...),
    session: Session = Depends(get_session)
):
    try:
        deal = session.get(Deal, deal_id)
        if not deal:
            raise HTTPException(404, "Deal introuvable")

        contact = session.get(Contact, deal.contact_id)
        if not contact:
            raise HTTPException(404, "Contact introuvable")

        # Enregistrer le message sortant
        msg = Message(
            deal_id=deal.id,
            contact_id=contact.id,
            direction="out",
            channel="WhatsApp",
            content=message,
            created_at=datetime.now(timezone.utc),
            sent_at=datetime.now(timezone.utc)
        )

        session.add(msg)
        session.commit()

        # Ici on peut appeler l'API Meta WhatsApp
        # (non inclus pour l’instant)

        return JSONResponse({"ok": True})

    finally:
        session.close()


# --------------------------------------------------------------------------
# Webhook WhatsApp
# --------------------------------------------------------------------------
@app.post("/webhook")
async def receive_webhook(request: Request, session: Session = Depends(get_session)):
    try:
        data = await request.json()

        # Chaque message entrant doit créer un enregistrement
        if "entry" in data:
            for entry in data["entry"]:
                if "changes" in entry:
                    for change in entry["changes"]:
                        value = change.get("value", {})
                        messages = value.get("messages", [])
                        if messages:
                            for m in messages:
                                phone = m["from"]
                                content = m.get("text", {}).get("body", "")

                                # Trouver contact associé
                                contact = session.exec(
                                    select(Contact).where(Contact.phone == phone)
                                ).first()

                                if contact:
                                    deal = session.exec(
                                        select(Deal).where(Deal.contact_id == contact.id)
                                    ).first()

                                    if deal:
                                        session.add(Message(
                                            deal_id=deal.id,
                                            contact_id=contact.id,
                                            direction="in",
                                            channel="WhatsApp",
                                            content=content,
                                            created_at=datetime.now(timezone.utc),
                                            sent_at=datetime.now(timezone.utc)
                                        ))
                                        session.commit()

        return JSONResponse({"status": "received"})

    finally:
        session.close()