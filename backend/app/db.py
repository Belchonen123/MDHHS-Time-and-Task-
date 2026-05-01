"""SQLite database layer (SQLAlchemy 2.x declarative)."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    event,
    inspect,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

# Resolve paths relative to backend/ (parent of app/)
_BACKEND_DIR = Path(__file__).resolve().parent.parent

if os.getenv("VERCEL"):
    # Vercel serverless functions mount project files read-only under /var/task.
    # SQLite and generated artifacts must live in /tmp for the invocation runtime.
    _RUNTIME_DIR = Path(tempfile.gettempdir()) / "mdhhs-poc-builder"
else:
    _RUNTIME_DIR = _BACKEND_DIR

DATA_DIR = _RUNTIME_DIR / "data"
STORAGE_DIR = _RUNTIME_DIR / "storage"
DATABASE_FILE = DATA_DIR / "clients.db"
DATABASE_URL = f"sqlite:///{DATABASE_FILE.as_posix()}"


class Base(DeclarativeBase):
    pass


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id: Mapped[str] = mapped_column(String(256), unique=True, index=True)
    client_name: Mapped[str] = mapped_column(String(512), default="")
    case_number: Mapped[str] = mapped_column(String(256), default="")
    county: Mapped[str] = mapped_column(String(256), default="")
    asw_name: Mapped[str] = mapped_column(String(256), default="")
    asw_email: Mapped[str] = mapped_column(String(256), default="")
    asw_phone: Mapped[str] = mapped_column(String(256), default="")
    pay_rate: Mapped[float] = mapped_column(Float, default=0.0)
    # From MDHHS-6064-P (persisted so rebuilt workbooks/PDF match authorization).
    provider_name: Mapped[str] = mapped_column(String(512), default="")
    auth_date: Mapped[str] = mapped_column(String(128), default="")
    # Per-day caregiver work window (JSON: Monday..Sunday -> {earliest, latest}).
    availability_json: Mapped[str] = mapped_column(Text, default="{}")
    # ASM 120 shared-living: other adults reside; optional 1/2 IADL proration unless waived.
    shared_living: Mapped[bool] = mapped_column(Boolean, default=False)
    iadl_separate_documented: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    plans: Mapped[list["Plan"]] = relationship(
        "Plan",
        back_populates="client",
        cascade="all, delete-orphan",
        order_by="Plan.version",
    )


class Plan(Base):
    __tablename__ = "plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_row_id: Mapped[int] = mapped_column(
        "client_id", ForeignKey("clients.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    # Target service month this plan covers (calendar-aware schedule). Default
    # 0 is a "legacy / unknown" sentinel — auto-backfilled from schedule_json
    # on read for plans created before this column existed.
    year: Mapped[int] = mapped_column(Integer, default=0)
    month: Mapped[int] = mapped_column(Integer, default=0)
    weekly_minutes: Mapped[int] = mapped_column(Integer, default=0)
    monthly_minutes: Mapped[float] = mapped_column(Float, default=0.0)
    monthly_amount: Mapped[float] = mapped_column(Float, default=0.0)
    schedule_json: Mapped[str] = mapped_column(Text, default="{}")
    tasks_json: Mapped[str] = mapped_column(Text, default="[]")
    # User-editable ScheduleConfig (per-task selected_weekdays /
    # selected_dates, and per-weekday start times). Empty "{}" means the
    # server synthesized defaults at generation time.
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    validation_json: Mapped[str] = mapped_column(Text, default="{}")
    validation_passed: Mapped[bool] = mapped_column(Boolean, default=False)
    source_pdf_path: Mapped[str] = mapped_column(String(1024), default="")
    xlsx_path: Mapped[str] = mapped_column(String(1024), default="")
    pdf_path: Mapped[str] = mapped_column(String(1024), default="")
    weekly_schedule_path: Mapped[str] = mapped_column(String(1024), default="")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    client: Mapped["Client"] = relationship(
        "Client", back_populates="plans", foreign_keys=[client_row_id]
    )


engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    future=True,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, future=True)


@event.listens_for(engine, "connect")
def _sqlite_enable_foreign_keys(dbapi_connection: object, _connection_record: object) -> None:
    cur = dbapi_connection.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


def _sqlite_ensure_client_updated_at() -> None:
    """Add missing columns on existing SQLite DBs (create_all does not alter tables)."""
    if engine.dialect.name != "sqlite":
        return
    if "clients" not in inspect(engine).get_table_names():
        return
    col_names = {c["name"] for c in inspect(engine).get_columns("clients")}
    if "updated_at" in col_names:
        return
    with engine.begin() as conn:
        conn.execute(
            text("ALTER TABLE clients ADD COLUMN updated_at TEXT")
        )
        conn.execute(
            text("UPDATE clients SET updated_at = created_at WHERE updated_at IS NULL")
        )


def _sqlite_ensure_plan_year_month() -> None:
    """Add year/month columns on existing SQLite DBs for calendar-aware plans."""
    if engine.dialect.name != "sqlite":
        return
    if "plans" not in inspect(engine).get_table_names():
        return
    col_names = {c["name"] for c in inspect(engine).get_columns("plans")}
    with engine.begin() as conn:
        if "year" not in col_names:
            conn.execute(text("ALTER TABLE plans ADD COLUMN year INTEGER DEFAULT 0"))
        if "month" not in col_names:
            conn.execute(text("ALTER TABLE plans ADD COLUMN month INTEGER DEFAULT 0"))


def _sqlite_ensure_plan_config_json() -> None:
    """Add config_json column on existing SQLite DBs for the ScheduleConfig editor."""
    if engine.dialect.name != "sqlite":
        return
    if "plans" not in inspect(engine).get_table_names():
        return
    col_names = {c["name"] for c in inspect(engine).get_columns("plans")}
    if "config_json" in col_names:
        return
    with engine.begin() as conn:
        conn.execute(
            text("ALTER TABLE plans ADD COLUMN config_json TEXT DEFAULT '{}'")
        )


def _sqlite_ensure_client_shared_living_flags() -> None:
    """Add ASM 120 shared-living flags on existing SQLite DBs."""
    if engine.dialect.name != "sqlite":
        return
    if "clients" not in inspect(engine).get_table_names():
        return
    col_names = {c["name"] for c in inspect(engine).get_columns("clients")}
    with engine.begin() as conn:
        if "shared_living" not in col_names:
            conn.execute(text("ALTER TABLE clients ADD COLUMN shared_living BOOLEAN DEFAULT 0"))
        if "iadl_separate_documented" not in col_names:
            conn.execute(
                text("ALTER TABLE clients ADD COLUMN iadl_separate_documented BOOLEAN DEFAULT 0")
            )


def _sqlite_ensure_client_availability_json() -> None:
    """Add availability_json on clients for worker time windows."""
    if engine.dialect.name != "sqlite":
        return
    if "clients" not in inspect(engine).get_table_names():
        return
    col_names = {c["name"] for c in inspect(engine).get_columns("clients")}
    if "availability_json" in col_names:
        return
    with engine.begin() as conn:
        conn.execute(
            text("ALTER TABLE clients ADD COLUMN availability_json TEXT DEFAULT '{}'")
        )


def _sqlite_ensure_client_provider_and_auth_fields() -> None:
    """Add provider/agency display name + auth date copied from extracted 6064-P."""
    if engine.dialect.name != "sqlite":
        return
    if "clients" not in inspect(engine).get_table_names():
        return
    col_names = {c["name"] for c in inspect(engine).get_columns("clients")}
    with engine.begin() as conn:
        if "provider_name" not in col_names:
            conn.execute(text("ALTER TABLE clients ADD COLUMN provider_name VARCHAR(512) DEFAULT ''"))
        if "auth_date" not in col_names:
            conn.execute(text("ALTER TABLE clients ADD COLUMN auth_date VARCHAR(128) DEFAULT ''"))


def _sqlite_ensure_plan_weekly_schedule_path() -> None:
    """Add weekly_schedule_path for the client-facing weekly workbook."""
    if engine.dialect.name != "sqlite":
        return
    if "plans" not in inspect(engine).get_table_names():
        return
    col_names = {c["name"] for c in inspect(engine).get_columns("plans")}
    if "weekly_schedule_path" in col_names:
        return
    with engine.begin() as conn:
        conn.execute(
            text("ALTER TABLE plans ADD COLUMN weekly_schedule_path TEXT DEFAULT ''")
        )


def init_db() -> None:
    """Create database file (if needed) and all tables."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=engine)
    _sqlite_ensure_client_updated_at()
    _sqlite_ensure_plan_year_month()
    _sqlite_ensure_plan_config_json()
    _sqlite_ensure_client_availability_json()
    _sqlite_ensure_client_shared_living_flags()
    _sqlite_ensure_client_provider_and_auth_fields()
    _sqlite_ensure_plan_weekly_schedule_path()
