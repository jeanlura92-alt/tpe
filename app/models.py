from __future__ import annotations
from typing import Optional
from datetime import datetime, timezone
from sqlmodel import SQLModel, Field


class DealStatus:
    NEW = "new"
    QUOTE = "quote"
    SCHEDULED = "scheduled"
    CLOSED = "closed"


class ContactType:
    CLIENT = "client"
    PROSPECT = "prospect"
    FOURNISSEUR = "fournisseur"
    AUTRE = "autre"


class MessageDirection:
    INBOUND = "in"   # reçu
    OUTBOUND = "out" # envoyé


class Contact(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    type: str = Field(default=ContactType.CLIENT, index=True)
    name: str
    phone: str = Field(index=True)  # +E.164
    email: Optional[str] = None
    company: Optional[str] = None
    address: Optional[str] = None
    tags: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Deal(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str
    contact_id: int = Field(foreign_key="contact.id", index=True)
    status: str = Field(default=DealStatus.NEW, index=True)
    amount_estimated: Optional[int] = None

    last_message_preview: Optional[str] = None
    last_message_channel: Optional[str] = None
    last_message_at: Optional[datetime] = None

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Message(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    deal_id: int = Field(foreign_key="deal.id", index=True)
    contact_id: int = Field(foreign_key="contact.id", index=True)
    direction: str = Field(index=True)         # "in" | "out"
    channel: str = Field(default="WhatsApp")
    content: str
    # timestamps
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    sent_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))  # <-- important (NOT NULL côté DB)
