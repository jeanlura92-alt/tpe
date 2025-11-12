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


def _pipeline_labels_for_profile(profile: Optional[str]) -> Dict[str, str]:
    base = {
        DealStatus.NEW: "Nouveaux messages",
        DealStatus.QUOTE: "Devis en cours",
        DealStatus.SCHEDULED: "Interventions planifiées",
        DealStatus.CLOSED: "Facturé / Clôturé",
    }
    if profile == ContactType.PROSPECT:
        return {
            DealStatus.NEW: "Nouveaux / À qualifier",
            DealStatus.QUOTE: "Devis envoyé",
            DealStatus.SCHEDULED: "Relance planifiée",
            DealStatus.CLOSED: "Gagné / Perdu",
        }
    if profile == ContactType.FOURNISSEUR:
        return {
            DealStatus.NEW: "Demandes fournisseurs",
            DealStatus.QUOTE: "Propositions reçues",
            DealStatus.SCHEDULED: "Commande en cours",
            DealStatus.CLOSED: "Réceptionnée / Clôturée",
        }
    if profile == ContactType.AUTRE:
        return {
            DealStatus.NEW: "Nouveaux",
            DealStatus.QUOTE: "À traiter",
            DealStatus.SCHEDULED: "Planifié",
            DealStatus.CLOSED: "Terminé",
        }
    return base


def _get_pipeline_columns(profile: Optional[str]) -> List[Dict[str, str]]:
    labels = _pipeline_labels_for_profile(profile)
    return [
        {"id": DealStatus.NEW, "label": labels[DealStatus.NEW]},
        {"id": DealStatus.QUOTE, "label": labels[DealStatus.QUOTE]},
        {"id": DealStatus.SCHEDULED, "label": labels[DealStatus.SCHEDULED]},
        {"id": DealStatus.CLOSED, "label": labels[DealStatus.CLOSED]},
    ]


# ====== UI: Dashboard / Kanban ======
@app.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    session: Session = Depends(get_session),
    contact_id: int | None = None,
    profile: str | None = None,  # client / prospect / fournisseur / autre
):
    profile = profile if profile in {ContactType.CLIENT, ContactType.PROSPECT, ContactType.FOURNISSEUR, ContactType.AUTRE} else None
    columns = _get_pipeline_columns(profile)

    stmt = select(Deal, Contact).join(Contact, Deal.contact_id == Contact.id)
    rows = session.exec(stmt).all()

    if profile:
        rows = [(d, c) for (d, c) in rows if c.type == profile]

    deals_by_status: Dict[str, List[Dict[str, Any]]] = {c["id"]: [] for c in columns}
    for deal, contact in rows:
        deals_by_status.get(deal.status, deals_by_status[DealStatus.NEW]).append({"deal": deal, "contact": contact})

    selected: Optional[Dict[str, Any]] = None
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

    contacts_list = session.exec(select(Contact).order_by(Contact.name)).all()
    status_options = [
        (DealStatus.NEW, _pipeline_labels_for_profile(profile)[DealStatus.NEW]),
        (DealStatus.QUOTE, _pipeline_labels_for_profile(profile)[DealStatus.QUOTE]),
        (DealStatus.SCHEDULED, _pipeline_labels_for_profile(profile)[DealStatus.SCHEDULED]),
        (DealStatus.CLOSED, _pipeline_labels_for_profile(profile)[DealStatus.CLOSED]),
    ]

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "columns": columns,
            "deals_by_status": deals_by_status,
            "selected": selected,
            "contacts_list": contacts_list,
            "status_options": status_options,
            "current_profile": profile or "tous",
        },
    )


# ====== Webhook / WhatsApp (stubs) ======
@app.post("/webhook/whatsapp")
async def whatsapp_webhook(payload: dict, session: Session = Depends(get_session)):
    return JSONResponse({"status": "ok", "detail": "webhook stub"})


@app.post("/deals/{deal_id}/send_message")
async def send_whatsapp_message(
    deal_id: int,
    content: str = Form(...),
    session: Session = Depends(get_session),
):
    content = (content or "").strip()
    if not content:
        return JSONResponse({"error": "message vide"}, status_code=400)

    deal = session.get(Deal, deal_id)
    if not deal:
        return JSONResponse({"error": "affaire inconnue"}, status_code=404)

    contact = session.get(Contact, deal.contact_id)
    if not contact:
        return JSONResponse({"error": "contact inconnu"}, status_code=404)

    return RedirectResponse(url=f"/?contact_id={contact.id}", status_code=303)


@app.post("/deals/{deal_id}/status")
def update_deal_status(
    request: Request,
    deal_id: int,
    status: str = Form(...),
    next: Optional[str] = Form(None),
    session: Session = Depends(get_session),
):
    """
    Supporte 2 modes :
    - Requête JS (fetch) -> renvoie JSON
    - Formulaire HTML -> redirige (303) vers next ou vers /?contact_id=...
    """
    if status not in {DealStatus.NEW, DealStatus.QUOTE, DealStatus.SCHEDULED, DealStatus.CLOSED}:
        return JSONResponse({"error": "statut invalide"}, status_code=400)

    deal = session.get(Deal, deal_id)
    if not deal:
        return JSONResponse({"error": "affaire inconnue"}, status_code=404)

    deal.status = status
    session.add(deal)
    session.commit()

    # Mode AJAX ?
    accept = (request.headers.get("accept") or "").lower()
    is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest" or "application/json" in accept

    if is_ajax:
        return JSONResponse({"ok": True, "deal_id": deal.id, "new_status": status})

    # Mode formulaire → redirect
    redirect_url = next or f"/?contact_id={deal.contact_id}"
    return RedirectResponse(url=redirect_url, status_code=303)


# ====== Contacts ======
@app.get("/contacts", response_class=HTMLResponse)
def contacts_list_view(request: Request, session: Session = Depends(get_session)):
    rows = session.exec(select(Contact).order_by(Contact.created_at.desc())).all()
    return templates.TemplateResponse("contacts.html", {"request": request, "contacts": rows})


@app.get("/contacts/new", response_class=HTMLResponse)
def contacts_new_form(request: Request):
    return templates.TemplateResponse(
        "contact_form.html",
        {
            "request": request,
            "mode": "new",
            "contact": None,
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
        type=type, name=name.strip(), phone=phone.strip(),
        email=(email or None), company=(company or None),
        address=(address or None), tags=(tags or None),
    )
    session.add(c)
    session.commit()
    session.refresh(c)

    d = Deal(title="Nouvelle affaire", contact_id=c.id, status=DealStatus.NEW)
    session.add(d)
    session.commit()
    return RedirectResponse(url="/contacts", status_code=303)


@app.get("/contacts/{contact_id}/edit", response_class=HTMLResponse)
def contacts_edit_form(contact_id: int, request: Request, session: Session = Depends(get_session)):
    contact = session.get(Contact, contact_id)
    if not contact:
        return HTMLResponse("Contact introuvable", status_code=404)
    return templates.TemplateResponse(
        "contact_form.html",
        {
            "request": request,
            "mode": "edit",
            "contact": contact,
            "contact_types": [
                ContactType.CLIENT,
                ContactType.PROSPECT,
                ContactType.FOURNISSEUR,
                ContactType.AUTRE,
            ],
        },
    )


@app.post("/contacts/{contact_id}/edit")
def contacts_update(
    contact_id: int,
    session: Session = Depends(get_session),
    type: str = Form(...),
    name: str = Form(...),
    phone: str = Form(...),
    email: str = Form(""),
    company: str = Form(""),
    address: str = Form(""),
    tags: str = Form(""),
):
    contact = session.get(Contact, contact_id)
    if not contact:
        return JSONResponse({"error": "contact introuvable"}, status_code=404)

    contact.type = type
    contact.name = name.strip()
    contact.phone = phone.strip()
    contact.email = (email or None)
    contact.company = (company or None)
    contact.address = (address or None)
    contact.tags = (tags or None)

    session.add(contact)
    session.commit()
    return RedirectResponse(url="/contacts", status_code=303)
