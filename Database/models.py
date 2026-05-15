"""
Chastiefol — SQLAlchemy ORM Models
Defines all database tables for trade persistence.

Tables:
- trades: Completed trade records
- signals: Incoming signal log (webhook + autonomous)
- account_snapshots: Periodic balance/equity snapshots (equity curve)
- daily_reports: End-of-day summary reports
"""

from datetime import datetime, timezone, date
from typing import Optional
from sqlalchemy import (
    Column, Integer, String, Float, Boolean, DateTime, Date,
    Text, JSON, Index, func
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """SQLAlchemy declarative base."""
    pass


class Trade(Base):
    """Completed trade record."""
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, default="XAUUSD")
    direction: Mapped[str] = mapped_column(String(10), nullable=False)  # BUY / SELL
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    exit_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    stop_loss: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    take_profit: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    lot_size: Mapped[float] = mapped_column(Float, nullable=False)
    pnl_usd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    outcome: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)  # WIN / LOSS
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    confluence: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(20), default="autonomous")  # webhook / autonomous
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        Index("idx_trades_symbol", "symbol"),
        Index("idx_trades_opened_at", "opened_at"),
        Index("idx_trades_outcome", "outcome"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "symbol": self.symbol,
            "direction": self.direction,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "lot_size": self.lot_size,
            "pnl_usd": self.pnl_usd,
            "outcome": self.outcome,
            "confidence": self.confidence,
            "confluence": self.confluence,
            "comment": self.comment,
            "source": self.source,
            "opened_at": self.opened_at.isoformat() if self.opened_at else None,
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
        }


class Signal(Base):
    """Incoming signal log."""
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, default="XAUUSD")
    action: Mapped[str] = mapped_column(String(10), nullable=False)  # BUY / SELL / CLOSE
    entry_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    sl_pips: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    tp_pips: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    confluence: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(20), default="webhook")
    executed: Mapped[bool] = mapped_column(Boolean, default=False)
    raw_payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        Index("idx_signals_created_at", "created_at"),
        Index("idx_signals_action", "action"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "symbol": self.symbol,
            "action": self.action,
            "entry_price": self.entry_price,
            "sl_pips": self.sl_pips,
            "tp_pips": self.tp_pips,
            "confluence": self.confluence,
            "confidence": self.confidence,
            "source": self.source,
            "executed": self.executed,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class AccountSnapshot(Base):
    """Periodic balance/equity snapshot for equity curve."""
    __tablename__ = "account_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    balance: Mapped[float] = mapped_column(Float, nullable=False)
    equity: Mapped[float] = mapped_column(Float, nullable=False)
    open_trades: Mapped[int] = mapped_column(Integer, default=0)
    daily_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    drawdown_pct: Mapped[float] = mapped_column(Float, default=0.0)
    snapshot_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        Index("idx_account_snapshots_at", "snapshot_at"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "balance": self.balance,
            "equity": self.equity,
            "open_trades": self.open_trades,
            "daily_pnl": self.daily_pnl,
            "drawdown_pct": self.drawdown_pct,
            "time": self.snapshot_at.isoformat() if self.snapshot_at else None,
        }


class DailyReport(Base):
    """End-of-day summary report."""
    __tablename__ = "daily_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_date: Mapped[date] = mapped_column(Date, nullable=False, unique=True)
    total_trades: Mapped[int] = mapped_column(Integer, default=0)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    losses: Mapped[int] = mapped_column(Integer, default=0)
    total_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    win_rate: Mapped[float] = mapped_column(Float, default=0.0)
    max_drawdown: Mapped[float] = mapped_column(Float, default=0.0)
    balance: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    report_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        Index("idx_daily_reports_date", "report_date"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "report_date": self.report_date.isoformat() if self.report_date else None,
            "total_trades": self.total_trades,
            "wins": self.wins,
            "losses": self.losses,
            "total_pnl": self.total_pnl,
            "win_rate": self.win_rate,
            "max_drawdown": self.max_drawdown,
            "balance": self.balance,
        }
