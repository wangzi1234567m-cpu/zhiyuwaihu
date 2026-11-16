from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import String, Integer, DateTime, ForeignKey, UniqueConstraint, create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from .config import settings

def now():
    return datetime.now(timezone.utc)

def new_id():
    return str(uuid4())

class Base(DeclarativeBase):
    pass

class Application(Base):
    __tablename__ = 'applications'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner: Mapped[str] = mapped_column(String(80), index=True)
    data: Mapped[dict] = mapped_column(JSONB)

class Knowledge(Base):
    __tablename__ = 'knowledge'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    version: Mapped[str] = mapped_column(String(80))
    data: Mapped[dict] = mapped_column(JSONB)

class Conversation(Base):
    __tablename__ = 'sessions'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    application_id: Mapped[str] = mapped_column(ForeignKey('applications.id'), index=True)
    owner: Mapped[str] = mapped_column(String(80), index=True)
    state: Mapped[str] = mapped_column(String(40), default='CASE_READY')
    revision: Mapped[int] = mapped_column(Integer, default=0)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    data: Mapped[dict] = mapped_column(JSONB)

class Message(Base):
    __tablename__ = 'messages'
    __table_args__ = (UniqueConstraint('session_id', 'position'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey('sessions.id'), index=True)
    position: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    data: Mapped[dict] = mapped_column(JSONB)

class EventRow(Base):
    __tablename__ = 'events'
    __table_args__ = (UniqueConstraint('session_id', 'seq'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey('sessions.id'), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    data: Mapped[dict] = mapped_column(JSONB)

class Operation(Base):
    __tablename__ = 'operations'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey('sessions.id'), index=True)
    key: Mapped[str] = mapped_column(String(200), unique=True)
    status: Mapped[str] = mapped_column(String(30), index=True)
    data: Mapped[dict] = mapped_column(JSONB)

class Effect(Base):
    """Mock业务系统的独立持久账本；业务成功与响应接收是两件事。"""
    __tablename__ = 'business_effects'
    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    operation_id: Mapped[str] = mapped_column(ForeignKey('operations.id'), unique=True)
    data: Mapped[dict] = mapped_column(JSONB)

class Record(Base):
    __tablename__ = 'call_records'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey('sessions.id'), unique=True)
    data: Mapped[dict] = mapped_column(JSONB)

class Confirmation(Base):
    __tablename__ = 'confirmations'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey('sessions.id'), index=True)
    data: Mapped[dict] = mapped_column(JSONB)

class RequestReceipt(Base):
    __tablename__ = 'request_receipts'
    key: Mapped[str] = mapped_column(String(300), primary_key=True)
    payload_hash: Mapped[str] = mapped_column(String(64))
    data: Mapped[dict] = mapped_column(JSONB)

engine = create_engine(settings.database_url, pool_pre_ping=True)
Session = sessionmaker(engine, expire_on_commit=False)
