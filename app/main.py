# app/main.py
from typing import List, Dict, Any, Optional

from fastapi import FastAPI, Depends, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import select, Session

from .database import engine, get_session, create_db_and_tables
from .models import Contact, Deal, DealStatus

app = FastAPI(title="CRM WhatsApp Artisans")

# templates
templates = Jinja2Templates(directory="app/templates")

# (optionnel) static si tu rajoutes du CSS/JS externe plus tard
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


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, session: Session = Depends(get_session)):
    columns = _get_pipeline_columns()

    # Récupère les deals + contact associé
    stmt = select(Deal, Contact).join(Contact, Deal.contact_id == Contact.id)
    rows = session.exec(stmt).all()

    deals_by_status: Dict[str, List[Dict[str, Any]]] = {
        col["id"]: [] for col in columns
    }

    for deal, contact in rows:
        deals_by_status.get(deal.status, deals_by_status[DealStatus.NEW]).append(
            {
                "deal": deal,
                "contact": contact,
            }
        )

    # Premier deal pour la partie droite (conversation) si dispo
    selected: Optional[Dict[str, Any]] = None
    for col in columns:
        if deals_by_status[col["id"]]:
            selected = deals_by_status[col["id"]][0]
            break

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "columns": columns,
            "deals_by_status": deals_by_status,
            "selected": selected,
        },
    )


@app.post("/webhook/whatsapp")
async def whatsapp_webhook(payload: dict, session: Session = Depends(get_session)):
    """
    Squelette de webhook pour WhatsApp Business API.
    - Ici tu recevras les messages entrants.
    - À implémenter : création/maj Contact, Deal, Message.
    """
    # TODO: parser payload, identifier numéro, retrouver/ créer le contact,
    #       créer ou mettre à jour un Deal, insérer un Message, etc.
    return JSONResponse({"status": "ok", "detail": "webhook stub"})


@app.post("/deals/{deal_id}/send_message")
async def send_whatsapp_message(
    deal_id: int,
    data: dict,
    session: Session = Depends(get_session),
):
    """
    Squelette d'envoi de message.
    data = { "content": "Texte à envoyer..." }
    """
    content = data.get("content", "").strip()
    if not content:
        return JSONResponse({"error": "message vide"}, status_code=400)

    deal = session.get(Deal, deal_id)
    if not deal:
        return JSONResponse({"error": "affaire inconnue"}, status_code=404)

    contact = session.get(Contact, deal.contact_id)
    if not contact:
        return JSONResponse({"error": "contact inconnu"}, status_code=404)

    # TODO : appeler l'API WhatsApp Business avec contact.phone + content
    # TODO : enregistrer le Message en base (direction = outbound)

    return JSONResponse({"status": "ok", "detail": "message envoyé (stub)"})
