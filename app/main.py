# app/main.py
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, Depends, Request, Form
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import select, Session
from .database import get_session, create_db_and_tables
from .models import Contact, Deal, DealStatus, ContactType

app = FastAPI(title="CRM WhatsApp Artisans")
templates = Jinja2Templates(directory="app/templates")
app.mount("/static", StaticFiles(directory="app/static", check_dir=False), name="static")


@app.on_event("startup")
def on_startup():
    create_db_and_tables()


def _get_pipeline_columns() -> List[Dict[str, str]]:
    return [
        {"id": DealStatus.NEW, "label": "Nouveaux messages"},
        {"id": DealStatus.QUOTE, "label": "Devis en cours"},
        {"id": DealStatus.SCHEDULED, "label": "Interventions planifiées"},
        {"id": DealStatus.CLOSED, "label": "Facturé / Clôturé"},
    ]


# ====== UI: Dashboard / Kanban ======
@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, session: Session = Depends(get_session), contact_id: int | None = None):
    columns = _get_pipeline_columns()
    stmt = select(Deal, Contact).join(Contact, Deal.contact_id == Contact.id)
    rows = session.exec(stmt).all()

    deals_by_status: Dict[str, List[Dict[str, Any]]] = {c["id"]: [] for c in columns}
    for deal, contact in rows:
        deals_by_status.get(deal.status, deals_by_status[DealStatus.NEW]).append(
            {"deal": deal, "contact": contact}
        )

    # sélection du contact actif
    selected = None
    if contact_id:
        for deal, contact in rows:
            if contact.id == contact_id:
                selected = {"deal": deal, "contact": contact}
                break
    else:
        for col in columns:
            if deals_by_status[col["id"]]:
                selected = deals_by_status[col["id"]][0]
                break

    # liste de tous les contacts (pour panneau latéral)
    contacts_list = session.exec(select(Contact).order_by(Contact.name)).all()

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "columns": columns,
            "deals_by_status": deals_by_status,
            "selected": selected,
            "contacts_list": contacts_list,
        },
    )


# ====== Webhook / Envoi WhatsApp (stubs) ======
@app.post("/webhook/whatsapp")
async def whatsapp_webhook(payload: dict, session: Session = Depends(get_session)):
    return JSONResponse({"status": "ok", "detail": "webhook stub"})


@app.post("/deals/{deal_id}/send_message")
async def send_whatsapp_message(
    deal_id: int,
    data: dict,
    session: Session = Depends(get_session),
):
    content = data.get("content", "").strip()
    if not content:
        return JSONResponse({"error": "message vide"}, status_code=400)

    deal = session.get(Deal, deal_id)
    if not deal:
        return JSONResponse({"error": "affaire inconnue"}, status_code=404)

    contact = session.get(Contact, deal.contact_id)
    if not contact:
        return JSONResponse({"error": "contact inconnu"}, status_code=404)

    return JSONResponse({"status": "ok", "detail": "message envoyé (stub)"})


# ====== Contacts: liste + création via formulaire ======
@app.get("/contacts", response_class=HTMLResponse)
def contacts_list(request: Request, session: Session = Depends(get_session)):
    rows = session.exec(select(Contact).order_by(Contact.created_at.desc())).all()
    return templates.TemplateResponse("contacts.html", {"request": request, "contacts": rows})


@app.get("/contacts/new", response_class=HTMLResponse)
def contacts_new_form(request: Request):
    return templates.TemplateResponse(
        "contact_form.html",
        {
            "request": request,
            "contact_types": [
                ContactType.CLIENT,
                ContactType.PROSPECT,
                ContactType.FOURNISSEUR,
                ContactType.AUTRE,
            ],
        },
    )


@app.post("/contacts/new")
def contacts_create(
    session: Session = Depends(get_session),
    type: str = Form(ContactType.CLIENT),
    name: str = Form(...),
    phone: str = Form(...),
    email: str = Form(""),
    company: str = Form(""),
    address: str = Form(""),
    tags: str = Form(""),
):
    c = Contact(
        type=type,
        name=name.strip(),
        phone=phone.strip(),
        email=(email or None),
        company=(company or None),
        address=(address or None),
        tags=(tags or None),
    )
    session.add(c)
    session.commit()
    session.refresh(c)

    d = Deal(title="Nouvelle affaire", contact_id=c.id, status=DealStatus.NEW)
    session.add(d)
    session.commit()
    return RedirectResponse(url="/contacts", status_code=303)


# ====== Deals: création rapide ======
@app.post("/deals/new")
def deals_create(
    session: Session = Depends(get_session),
    contact_id: int = Form(...),
    title: str = Form(...),
    status: str = Form(DealStatus.NEW),
    amount_estimated: Optional[float] = Form(None),
):
    contact = session.get(Contact, contact_id)
    if not contact:
        return JSONResponse({"error": "contact introuvable"}, status_code=404)

    d = Deal(title=(title.strip() or "Affaire"), contact_id=contact_id, status=status, amount_estimated=amount_estimated)
    session.add(d)
    session.commit()
    session.refresh(d)
    return JSONResponse({"ok": True, "deal_id": d.id})


# ====== Seed de démo ======
@app.post("/dev/seed")
def dev_seed(session: Session = Depends(get_session)):
    if session.exec(select(Contact).limit(1)).first():
        return {"ok": True, "detail": "Déjà des données."}
    c1 = Contact(type=ContactType.CLIENT, name="Mme Dupont", phone="+966555555501", tags="Urgent,Nouveau")
    c2 = Contact(type=ContactType.PROSPECT, name="M. Leroy", phone="+966555555502", tags="Urgent")
    c3 = Contact(type=ContactType.CLIENT, name="Mme Ben Ali", phone="+966555555503", tags="Rappel,Chauffage")
    session.add_all([c1, c2, c3]); session.commit()
    d1 = Deal(title="Couleur + brushing samedi ?", contact_id=c1.id, status=DealStatus.NEW, amount_estimated=80, last_message_preview="Bonjour, dispo samedi ?", last_message_channel="WhatsApp")
    d2 = Deal(title="Urgence plomberie – fuite cuisine", contact_id=c2.id, status=DealStatus.NEW)
    d3 = Deal(title="Entretien chaudière – rappel", contact_id=c3.id, status=DealStatus.QUOTE, amount_estimated=120)
    session.add_all([d1, d2, d3]); session.commit()
    return {"ok": True, "contacts": 3, "deals": 3}
