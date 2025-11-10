# app/models.py
from datetime import datetime
from typing import Optional

from sqlmodel import SQLModel, Field


class ContactType:
    CLIENT = "client"
    PROSPECT = "prospect"
    FOURNISSEUR = "fournisseur"
    AUTRE = "autre"


class DealStatus:
    NEW = "new"             # Nouveaux messages
    QUOTE = "quote"         # Devis en cours
    SCHEDULED = "scheduled" # Interventions planifiées
    CLOSED = "closed"       # Facturé / Clôturé


class Contact(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    type: str = Field(
        default=ContactType.CLIENT,
        description="client/prospect/fournisseur/autre"
    )
    name: str
    phone: str
    email: Optional[str] = None
    company: Optional[str] = None
    address: Optional[str] = None
    tags: Optional[str] = Field(
        default=None, description="tags séparés par des virgules"
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Deal(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str
    status: str = Field(
        default=DealStatus.NEW,
        description="new/quote/scheduled/closed"
    )
    contact_id: int = Field(foreign_key="contact.id")
    amount_estimated: Optional[float] = None
    last_message_preview: Optional[str] = None
    last_message_channel: Optional[str] = Field(
        default="WhatsApp", description="WhatsApp / téléphone / email..."
    )
    last_message_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class MessageDirection:
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class Message(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    deal_id: int = Field(foreign_key="deal.id")
    contact_id: int = Field(foreign_key="contact.id")
    direction: str = Field(
        default=MessageDirection.INBOUND,
        description="inbound = client -> artisan, outbound = artisan -> client"
    )
    content: str
    sent_at: datetime = Field(default_factory=datetime.utcnow)
