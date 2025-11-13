from datetime import datetime, timezone
import os
from typing import Dict, List, Optional, Tuple

from fastapi import FastAPI, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlmodel import Session, select

from .database import get_session, create_db_and_tables
from .models import Contact, Deal, Message

# ---------- App & Templating ----------
app = FastAPI()

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
env = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR),
    autoescape=select_autoescape(["html", "xml"])
)

# Static (CSS/JS)
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# ---------- Helpers ----------
def render_template(name: str, context: dict) -> HTMLResponse:
    template = env.get_template(name)
    html = template.render(**context)
    return HTMLResponse(html)

def now_utc() -> datetime:
    return datetime.now(timezone.utc)

# Tous les statuts/colonnes disponibles pour le kanban
KANBAN_COLUMNS: List[Tuple[str, str]] = [
    ("new", "Nouveau"),
    ("to_do", "À traiter"),
    ("in_progress", "En cours"),
    ("won", "Gagné"),
    ("lost", "Perdu"),
]

PROFILE_ALLOWED = {"client", "prospect", "fournisseur", "autre"}

def current_profile_from_query(profile: Optional[str]) -> Optional[str]:
    if profile and profile in PROFILE_ALLOWED:
        return profile
    return None

# ---------- Startup ----------
@app.on_event("startup")
def on_startup():
    # Crée tables si besoin
    create_db_and_tables()

# ---------- Pages ----------
@app.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    contact_id: Optional[int] = None,
    profile: Optional[str] = None,
    msgs_before: Optional[int] = None,
    msgs_limit: int = 30,
    session: Session = Depends(get_session),
):
    """
    Dashboard principal (kanban + panneau droit).
    - Filtrage du kanban par profil (client/prospect/fournisseur/autre)
    - Panneau droit: liste contacts + thread WhatsApp du contact sélectionné
    """
    current_profile = current_profile_from_query(profile)

    # 1) Liste des contacts (pour le panneau droit)
    if current_profile:
        contacts_stmt = select(Contact).where(Contact.type == current_profile).order_by(Contact.created_at.desc())
    else:
        contacts_stmt = select(Contact).order_by(Contact.created_at.desc())
    all_contacts: List[Contact] = session.exec(contacts_stmt).all()

    # 2) Préparer le kanban si profil choisi
    deals_by_status: Dict[str, List[Dict]] = {k: [] for k, _ in KANBAN_COLUMNS}
    if current_profile:
        stmt = (
            select(Deal, Contact)
            .join(Contact, Deal.contact_id == Contact.id)
            .where(Contact.type == current_profile)
            .order_by(Deal.created_at.desc())
        )
        rows = session.exec(stmt).all()
        for d, c in rows:
            bucket = d.status if d.status in deals_by_status else "new"
            deals_by_status[bucket].append({"deal": d, "contact": c})

    # 3) Contact sélectionné (panneau droit) + messages
    selected = None
    messages = []
    messages_next_cursor = None

    if contact_id:
        contact = session.get(Contact, contact_id)
        if not contact:
            raise HTTPException(status_code=404, detail="Contact introuvable")

        # Récupérer ou créer le deal associé au contact (un deal par contact)
        deal_stmt = select(Deal).where(Deal.contact_id == contact.id).order_by(Deal.created_at.desc())
        deal = session.exec(deal_stmt).first()
        if not deal:
            deal = Deal(
                title=f"Deal {contact.name}",
                status="new",
                contact_id=contact.id,
                created_at=now_utc(),
            )
            session.add(deal)
            session.commit()
            session.refresh(deal)

        # Messages — ordre chronologique
        msg_stmt = select(Message).where(Message.deal_id == deal.id).order_by(Message.created_at.asc())
        if msgs_limit:
            msg_stmt = msg_stmt.limit(max(5, min(200, msgs_limit)))
        messages = session.exec(msg_stmt).all()

        selected = {"deal": deal, "contact": contact}

    # Compteurs
    total_contacts = len(all_contacts)
    total_deals = sum(len(v) for v in deals_by_status.values()) if current_profile else 0

    # Colonnes kanban pour le template
    columns = [{"id": c[0], "label": c[1]} for c in KANBAN_COLUMNS]

    return render_template(
        "dashboard.html",
        {
            "request": request,  # pour compat jinja si tu utilises `url_for`
            "columns": columns,
            "deals_by_status": deals_by_status,
            "current_profile": current_profile,
            "all_contacts": all_contacts,
            "contacts": all_contacts,  # alias utilisé dans certains templates
            "selected": selected,
            "messages": messages,
            "messages_next_cursor": messages_next_cursor,
            "messages_limit": msgs_limit,
            "total_contacts": total_contacts,
            "total_deals": total_deals,
        },
    )

@app.get("/contacts", response_class=HTMLResponse)
def contacts_page(
    request: Request,
    session: Session = Depends(get_session),
):
    stmt = select(Contact).order_by(Contact.created_at.desc())
    contacts = session.exec(stmt).all()
    return render_template("contacts.html", {"request": request, "contacts": contacts})

@app.get("/contacts/new", response_class=HTMLResponse)
def contacts_new_form(
    request: Request,
):
    # 🔧 Correction : on passe aussi `contact=None` pour que le template ne plante pas
    return render_template(
        "contact_form.html",
        {"request": request, "mode": "create", "contact": None},
    )

@app.post("/contacts/new")
def contacts_create(
    name: str = Form(...),
    phone: str = Form(""),
    email: str = Form(""),
    type: str = Form("client"),
    company: str = Form(""),
    address: str = Form(""),
    tags: str = Form(""),
    session: Session = Depends(get_session),
):
    if type not in PROFILE_ALLOWED:
        type = "autre"

    c = Contact(
        name=name.strip(),
        phone=phone.strip() or None,
        email=email.strip() or None,
        type=type,
        company=company.strip() or None,
        address=address.strip() or None,
        tags=tags.strip() or None,
        created_at=now_utc(),
    )
    session.add(c)
    session.commit()
    session.refresh(c)

    # Créer un deal par défaut sur "new"
    d = Deal(
        title=f"Deal {c.name}",
        status="new",
        contact_id=c.id,
        created_at=now_utc(),
    )
    session.add(d)
    session.commit()

    # Rediriger vers dashboard filtré sur ce profil et ce contact
    redirect_url = f"/?contact_id={c.id}&profile={c.type}"
    return RedirectResponse(redirect_url, status_code=303)

# ---------- Mises à jour ----------
@app.post("/deals/{deal_id}/send_message")
def send_whatsapp_message(
    deal_id: int,
    content: str = Form(...),
    session: Session = Depends(get_session),
):
    deal = session.get(Deal, deal_id)
    if not deal:
        raise HTTPException(status_code=404, detail="Deal introuvable")

    contact = session.get(Contact, deal.contact_id)
    if not contact:
        raise HTTPException(status_code=404, detail="Contact introuvable")

    # Enregistre le message OUT (et marque sent_at pour éviter la contrainte NOT NULL)
    ts = now_utc()
    msg = Message(
        deal_id=deal.id,
        contact_id=contact.id,
        direction="out",
        channel="WhatsApp",
        content=content.strip(),
        created_at=ts,
        sent_at=ts,  # <-- IMPORTANT pour éviter l'erreur NOT NULL
    )
    session.add(msg)

    # Met à jour le résumé sur le deal
    deal.last_message_preview = (content or "")[:140]
    deal.last_message_channel = "WhatsApp"
    deal.last_message_at = ts
    session.add(deal)

    session.commit()

    return RedirectResponse(
        f"/?contact_id={contact.id}&profile={contact.type}",
        status_code=303,
    )

@app.post("/deals/{deal_id}/status")
def update_deal_status(
    deal_id: int,
    status: str = Form(...),
    request: Request = None,
    session: Session = Depends(get_session),
):
    if status not in {k for k, _ in KANBAN_COLUMNS}:
        raise HTTPException(status_code=400, detail="Statut invalide")

    deal = session.get(Deal, deal_id)
    if not deal:
        raise HTTPException(status_code=404, detail="Deal introuvable")

    deal.status = status
    session.add(deal)
    session.commit()

    # JSON si AJAX ; redirect sinon
    is_ajax = False
    try:
        is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    except Exception:
        pass

    if is_ajax:
        return JSONResponse({"ok": True, "deal_id": deal_id, "new_status": status})
    return RedirectResponse("/", status_code=303)

# ---------- Health / Root HEAD ----------
@app.head("/", response_class=PlainTextResponse)
def head_root():
    # Render fait un HEAD pour healthcheck, renvoyer 200
    return PlainTextResponse("", status_code=200)