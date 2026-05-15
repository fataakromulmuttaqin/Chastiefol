"""
Chastiefol — Database Repository Layer
Async SQLAlchemy operations for trade persistence.

Provides:
- DatabaseManager: connection lifecycle, table creation
- TradeRepository: CRUD for trades
- SignalRepository: CRUD for signals
- SnapshotRepository: equity curve snapshots
"""

import logging
import os
from datetime import datetime, timezone, date, timedelta
from typing import Optional, List, Dict

from sqlalchemy.ext.asyncio import (
    create_async_engine,
    async_sessionmaker,
    AsyncSession,
    AsyncEngine,
)
from sqlalchemy import select, desc, func, and_, delete
from sqlalchemy.dialects.postgresql import insert as pg_insert

from .models import Base, Trade, Signal, AccountSnapshot, DailyReport

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("Database")


# ──────────────────────────────────────────────
# Database Manager
# ──────────────────────────────────────────────

class DatabaseManager:
    """
    Manages async database connections and session lifecycle.

    Usage:
        db = DatabaseManager("postgresql+asyncpg://user:pass@localhost/chastiefol")
        await db.initialize()

        async with db.session() as session:
            # use session...

        await db.close()
    """

    def __init__(self, database_url: str = None):
        self.database_url = database_url or os.getenv(
            "DATABASE_URL",
            "postgresql+asyncpg://chastiefol:chastiefol_pass@localhost:5432/chastiefol"
        )
        # Convert postgres:// to postgresql+asyncpg:// if needed
        if self.database_url.startswith("postgres://"):
            self.database_url = self.database_url.replace("postgres://", "postgresql+asyncpg://", 1)
        elif self.database_url.startswith("postgresql://"):
            self.database_url = self.database_url.replace("postgresql://", "postgresql+asyncpg://", 1)

        self._engine: Optional[AsyncEngine] = None
        self._session_factory: Optional[async_sessionmaker] = None
        self._initialized = False

    async def initialize(self):
        """Create engine, session factory, and tables."""
        self._engine = create_async_engine(
            self.database_url,
            echo=False,
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
        )
        self._session_factory = async_sessionmaker(
            self._engine, class_=AsyncSession, expire_on_commit=False
        )

        # Create tables if they don't exist
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        self._initialized = True
        log.info(f"Database initialized | URL: {self._mask_url()}")

    async def close(self):
        """Close the engine and all connections."""
        if self._engine:
            await self._engine.dispose()
            self._initialized = False
            log.info("Database connections closed.")

    def session(self) -> AsyncSession:
        """Get a new async session."""
        if not self._session_factory:
            raise RuntimeError("Database not initialized. Call initialize() first.")
        return self._session_factory()

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    def _mask_url(self) -> str:
        """Mask password in URL for logging."""
        url = self.database_url
        if "@" in url:
            parts = url.split("@")
            creds = parts[0].split("://")[-1]
            if ":" in creds:
                user = creds.split(":")[0]
                return url.replace(creds, f"{user}:****")
        return url


# ──────────────────────────────────────────────
# Trade Repository
# ──────────────────────────────────────────────

class TradeRepository:
    """CRUD operations for trades."""

    def __init__(self, db: DatabaseManager):
        self.db = db

    async def create(self, trade_data: dict) -> Trade:
        """Insert a new trade record."""
        async with self.db.session() as session:
            trade = Trade(**trade_data)
            session.add(trade)
            await session.commit()
            await session.refresh(trade)
            log.debug(f"Trade created: #{trade.id} {trade.direction} {trade.symbol}")
            return trade

    async def close_trade(self, trade_id: int, exit_price: float,
                          pnl: float, outcome: str) -> Optional[Trade]:
        """Update a trade with exit data."""
        async with self.db.session() as session:
            result = await session.execute(
                select(Trade).where(Trade.id == trade_id)
            )
            trade = result.scalar_one_or_none()
            if trade:
                trade.exit_price = exit_price
                trade.pnl_usd = pnl
                trade.outcome = outcome
                trade.closed_at = datetime.now(timezone.utc)
                await session.commit()
                log.debug(f"Trade closed: #{trade_id} {outcome} P&L=${pnl:.2f}")
            return trade

    async def get_recent(self, limit: int = 50, symbol: str = None) -> List[dict]:
        """Get recent trades, newest first."""
        async with self.db.session() as session:
            query = select(Trade).order_by(desc(Trade.opened_at)).limit(limit)
            if symbol:
                query = query.where(Trade.symbol == symbol)
            result = await session.execute(query)
            trades = result.scalars().all()
            return [t.to_dict() for t in trades]

    async def get_by_date_range(self, start: datetime, end: datetime) -> List[dict]:
        """Get trades within a date range."""
        async with self.db.session() as session:
            result = await session.execute(
                select(Trade).where(
                    and_(Trade.opened_at >= start, Trade.opened_at <= end)
                ).order_by(desc(Trade.opened_at))
            )
            return [t.to_dict() for t in result.scalars().all()]

    async def get_stats(self, symbol: str = None) -> dict:
        """Get aggregate trade statistics."""
        async with self.db.session() as session:
            query = select(Trade).where(Trade.outcome.isnot(None))
            if symbol:
                query = query.where(Trade.symbol == symbol)
            result = await session.execute(query)
            trades = result.scalars().all()

            if not trades:
                return {"total": 0, "wins": 0, "losses": 0, "win_rate": 0, "total_pnl": 0}

            wins = sum(1 for t in trades if t.outcome == "WIN")
            losses = sum(1 for t in trades if t.outcome == "LOSS")
            total_pnl = sum(t.pnl_usd or 0 for t in trades)

            return {
                "total": len(trades),
                "wins": wins,
                "losses": losses,
                "win_rate": round(wins / len(trades) * 100, 1) if trades else 0,
                "total_pnl": round(total_pnl, 2),
                "avg_win": round(
                    sum(t.pnl_usd for t in trades if t.outcome == "WIN" and t.pnl_usd) / max(wins, 1), 2
                ),
                "avg_loss": round(
                    sum(t.pnl_usd for t in trades if t.outcome == "LOSS" and t.pnl_usd) / max(losses, 1), 2
                ),
            }


# ──────────────────────────────────────────────
# Signal Repository
# ──────────────────────────────────────────────

class SignalRepository:
    """CRUD operations for signals."""

    def __init__(self, db: DatabaseManager):
        self.db = db

    async def create(self, signal_data: dict) -> Signal:
        """Log a new signal."""
        async with self.db.session() as session:
            signal = Signal(**signal_data)
            session.add(signal)
            await session.commit()
            await session.refresh(signal)
            return signal

    async def mark_executed(self, signal_id: int):
        """Mark a signal as executed."""
        async with self.db.session() as session:
            result = await session.execute(
                select(Signal).where(Signal.id == signal_id)
            )
            signal = result.scalar_one_or_none()
            if signal:
                signal.executed = True
                await session.commit()

    async def get_recent(self, limit: int = 20) -> List[dict]:
        """Get recent signals."""
        async with self.db.session() as session:
            result = await session.execute(
                select(Signal).order_by(desc(Signal.created_at)).limit(limit)
            )
            return [s.to_dict() for s in result.scalars().all()]

    async def get_unexecuted(self) -> List[dict]:
        """Get signals that haven't been executed yet."""
        async with self.db.session() as session:
            result = await session.execute(
                select(Signal).where(Signal.executed == False).order_by(Signal.created_at)
            )
            return [s.to_dict() for s in result.scalars().all()]


# ──────────────────────────────────────────────
# Snapshot Repository (Equity Curve)
# ──────────────────────────────────────────────

class SnapshotRepository:
    """CRUD for account snapshots (equity curve data)."""

    def __init__(self, db: DatabaseManager):
        self.db = db

    async def create(self, balance: float, equity: float,
                     open_trades: int = 0, daily_pnl: float = 0,
                     drawdown_pct: float = 0) -> AccountSnapshot:
        """Save a new account snapshot."""
        async with self.db.session() as session:
            snapshot = AccountSnapshot(
                balance=balance,
                equity=equity,
                open_trades=open_trades,
                daily_pnl=daily_pnl,
                drawdown_pct=drawdown_pct,
            )
            session.add(snapshot)
            await session.commit()
            return snapshot

    async def get_equity_curve(self, days: int = 30) -> List[dict]:
        """Get equity curve data for the last N days."""
        since = datetime.now(timezone.utc) - timedelta(days=days)
        async with self.db.session() as session:
            result = await session.execute(
                select(AccountSnapshot)
                .where(AccountSnapshot.snapshot_at >= since)
                .order_by(AccountSnapshot.snapshot_at)
            )
            return [s.to_dict() for s in result.scalars().all()]

    async def get_latest(self) -> Optional[dict]:
        """Get the most recent snapshot."""
        async with self.db.session() as session:
            result = await session.execute(
                select(AccountSnapshot).order_by(desc(AccountSnapshot.snapshot_at)).limit(1)
            )
            snapshot = result.scalar_one_or_none()
            return snapshot.to_dict() if snapshot else None

    async def cleanup_old(self, keep_days: int = 90):
        """Delete snapshots older than N days."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=keep_days)
        async with self.db.session() as session:
            await session.execute(
                delete(AccountSnapshot).where(AccountSnapshot.snapshot_at < cutoff)
            )
            await session.commit()
            log.info(f"Cleaned up snapshots older than {keep_days} days")


# ──────────────────────────────────────────────
# Daily Report Repository
# ──────────────────────────────────────────────

class DailyReportRepository:
    """CRUD for daily reports."""

    def __init__(self, db: DatabaseManager):
        self.db = db

    async def save_report(self, report_data: dict) -> DailyReport:
        """Save or update a daily report (upsert by date)."""
        async with self.db.session() as session:
            report_date = report_data.get("report_date", date.today())
            if isinstance(report_date, str):
                report_date = date.fromisoformat(report_date)

            # Check if report exists for this date
            result = await session.execute(
                select(DailyReport).where(DailyReport.report_date == report_date)
            )
            existing = result.scalar_one_or_none()

            if existing:
                existing.total_trades = report_data.get("total_trades", 0)
                existing.wins = report_data.get("wins", 0)
                existing.losses = report_data.get("losses", 0)
                existing.total_pnl = report_data.get("total_pnl", 0)
                existing.win_rate = report_data.get("win_rate", 0)
                existing.max_drawdown = report_data.get("max_drawdown", 0)
                existing.balance = report_data.get("balance")
                existing.report_json = report_data
                await session.commit()
                return existing
            else:
                report = DailyReport(
                    report_date=report_date,
                    total_trades=report_data.get("total_trades", 0),
                    wins=report_data.get("wins", 0),
                    losses=report_data.get("losses", 0),
                    total_pnl=report_data.get("total_pnl", 0),
                    win_rate=report_data.get("win_rate", 0),
                    max_drawdown=report_data.get("max_drawdown", 0),
                    balance=report_data.get("balance"),
                    report_json=report_data,
                )
                session.add(report)
                await session.commit()
                await session.refresh(report)
                return report

    async def get_recent(self, days: int = 30) -> List[dict]:
        """Get recent daily reports."""
        since = date.today() - timedelta(days=days)
        async with self.db.session() as session:
            result = await session.execute(
                select(DailyReport)
                .where(DailyReport.report_date >= since)
                .order_by(desc(DailyReport.report_date))
            )
            return [r.to_dict() for r in result.scalars().all()]

    async def get_by_date(self, report_date: date) -> Optional[dict]:
        """Get report for a specific date."""
        async with self.db.session() as session:
            result = await session.execute(
                select(DailyReport).where(DailyReport.report_date == report_date)
            )
            report = result.scalar_one_or_none()
            return report.to_dict() if report else None
