"""
Chastiefol — Automated Daily Reports
Generates end-of-day trading summaries and delivers them via Telegram + DB.

Features:
- Daily P&L breakdown (per-pair and total)
- Win/loss statistics and win rate
- Equity curve progression
- Risk metrics (drawdown, max risk used)
- Best/worst trade of the day
- Weekly and monthly summaries
- Scheduled execution (configurable time)
- Telegram formatted delivery
- Database persistence for historical review
"""

import logging
import asyncio
from datetime import datetime, timezone, date, timedelta, time as dt_time
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Callable, Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("Reports")


# ──────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────

@dataclass
class ReportConfig:
    """Configuration for automated report generation."""
    # Schedule
    daily_report_time_utc: str = "21:30"  # Generate daily report at this UTC time
    weekly_report_day: int = 4            # 0=Mon, 4=Fri
    weekly_report_time_utc: str = "22:00"

    # Content
    include_per_pair: bool = True
    include_best_worst: bool = True
    include_equity_change: bool = True
    include_risk_metrics: bool = True
    include_open_positions: bool = True
    include_weekly_summary: bool = True

    # Delivery
    send_telegram: bool = True
    save_to_db: bool = True
    save_to_file: bool = False
    file_path: str = "reports/"

    # Thresholds for highlighting
    good_day_threshold: float = 100.0     # USD profit to mark as "good day"
    bad_day_threshold: float = -50.0      # USD loss to mark as "bad day"


# ──────────────────────────────────────────────
# Report Data Structures
# ──────────────────────────────────────────────

@dataclass
class TradeStats:
    """Aggregated trade statistics."""
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    breakeven: int = 0
    total_pnl: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    largest_win: float = 0.0
    largest_loss: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_rr: float = 0.0

    @classmethod
    def from_trades(cls, trades: List[dict]) -> "TradeStats":
        """Calculate stats from list of trade dicts."""
        if not trades:
            return cls()

        stats = cls()
        stats.total_trades = len(trades)

        wins = [t for t in trades if (t.get("pnl_usd", 0) or 0) > 0]
        losses = [t for t in trades if (t.get("pnl_usd", 0) or 0) < 0]
        be = [t for t in trades if (t.get("pnl_usd", 0) or 0) == 0]

        stats.wins = len(wins)
        stats.losses = len(losses)
        stats.breakeven = len(be)

        pnls = [t.get("pnl_usd", 0) or 0 for t in trades]
        stats.total_pnl = sum(pnls)
        stats.gross_profit = sum(p for p in pnls if p > 0)
        stats.gross_loss = abs(sum(p for p in pnls if p < 0))

        stats.avg_win = stats.gross_profit / max(stats.wins, 1)
        stats.avg_loss = stats.gross_loss / max(stats.losses, 1)
        stats.largest_win = max(pnls) if pnls else 0
        stats.largest_loss = min(pnls) if pnls else 0

        stats.win_rate = stats.wins / max(stats.total_trades, 1) * 100
        stats.profit_factor = stats.gross_profit / max(stats.gross_loss, 0.01)

        return stats


@dataclass
class DailyReportData:
    """Complete daily report data."""
    report_date: date = field(default_factory=date.today)
    generated_at: str = ""

    # Account
    balance: float = 0.0
    equity: float = 0.0
    daily_pnl: float = 0.0
    balance_change_pct: float = 0.0
    peak_balance: float = 0.0
    drawdown_pct: float = 0.0

    # Trades
    stats: TradeStats = field(default_factory=TradeStats)
    per_pair_stats: Dict[str, TradeStats] = field(default_factory=dict)

    # Best/Worst
    best_trade: Optional[dict] = None
    worst_trade: Optional[dict] = None

    # Open positions
    open_positions: int = 0
    open_pnl: float = 0.0

    # Risk
    max_risk_used_pct: float = 0.0
    max_drawdown_today: float = 0.0
    circuit_breaker_triggered: bool = False

    # Week context
    weekly_pnl: float = 0.0
    weekly_trades: int = 0
    weekly_win_rate: float = 0.0

    def to_dict(self) -> dict:
        return {
            "report_date": self.report_date.isoformat(),
            "generated_at": self.generated_at,
            "balance": round(self.balance, 2),
            "equity": round(self.equity, 2),
            "daily_pnl": round(self.daily_pnl, 2),
            "balance_change_pct": round(self.balance_change_pct, 2),
            "drawdown_pct": round(self.drawdown_pct, 2),
            "total_trades": self.stats.total_trades,
            "wins": self.stats.wins,
            "losses": self.stats.losses,
            "win_rate": round(self.stats.win_rate, 1),
            "profit_factor": round(self.stats.profit_factor, 2),
            "largest_win": round(self.stats.largest_win, 2),
            "largest_loss": round(self.stats.largest_loss, 2),
            "max_drawdown": round(self.max_drawdown_today, 2),
            "open_positions": self.open_positions,
            "weekly_pnl": round(self.weekly_pnl, 2),
        }


# ──────────────────────────────────────────────
# Daily Report Generator
# ──────────────────────────────────────────────

class DailyReportGenerator:
    """
    Generates comprehensive daily trading reports.

    Usage:
        generator = DailyReportGenerator(config)
        generator.set_data_sources(
            get_trades=lambda: [...],
            get_account=lambda: {...},
            get_portfolio=lambda: {...},
        )
        report = await generator.generate()
        message = generator.format_telegram(report)
    """

    def __init__(self, config: ReportConfig = None):
        self.config = config or ReportConfig()

        # Data source callbacks
        self._get_trades: Optional[Callable] = None
        self._get_account: Optional[Callable] = None
        self._get_portfolio: Optional[Callable] = None
        self._get_weekly_trades: Optional[Callable] = None

        # Delivery callbacks
        self._send_telegram: Optional[Callable] = None
        self._save_to_db: Optional[Callable] = None

    def set_data_sources(self, get_trades=None, get_account=None,
                         get_portfolio=None, get_weekly_trades=None):
        """Set data source callbacks."""
        self._get_trades = get_trades
        self._get_account = get_account
        self._get_portfolio = get_portfolio
        self._get_weekly_trades = get_weekly_trades

    def set_delivery(self, send_telegram=None, save_to_db=None):
        """Set delivery callbacks."""
        self._send_telegram = send_telegram
        self._save_to_db = save_to_db

    async def generate(self, target_date: date = None) -> DailyReportData:
        """Generate daily report for the given date (default: today)."""
        report = DailyReportData()
        report.report_date = target_date or date.today()
        report.generated_at = datetime.now(timezone.utc).isoformat()

        # Get today's trades
        trades = []
        if self._get_trades:
            result = self._get_trades()
            if asyncio.iscoroutine(result):
                result = await result
            trades = result or []

        # Calculate stats
        report.stats = TradeStats.from_trades(trades)
        report.daily_pnl = report.stats.total_pnl

        # Per-pair breakdown
        if self.config.include_per_pair and trades:
            pairs = set(t.get("symbol", "XAUUSD") for t in trades)
            for pair in pairs:
                pair_trades = [t for t in trades if t.get("symbol", "XAUUSD") == pair]
                report.per_pair_stats[pair] = TradeStats.from_trades(pair_trades)

        # Best/worst trade
        if self.config.include_best_worst and trades:
            pnl_trades = [t for t in trades if t.get("pnl_usd") is not None]
            if pnl_trades:
                report.best_trade = max(pnl_trades, key=lambda t: t.get("pnl_usd", 0))
                report.worst_trade = min(pnl_trades, key=lambda t: t.get("pnl_usd", 0))

        # Account data
        if self._get_account:
            account = self._get_account()
            if asyncio.iscoroutine(account):
                account = await account
            if account:
                report.balance = account.get("balance", 0)
                report.equity = account.get("equity", report.balance)
                report.peak_balance = account.get("peak_balance", report.balance)
                report.drawdown_pct = account.get("drawdown_pct", 0)
                report.open_positions = account.get("open_trades", 0)
                report.max_risk_used_pct = account.get("total_risk_pct", 0)
                report.max_drawdown_today = account.get("max_drawdown_pct", 0)
                # Balance change %
                prev_balance = report.balance - report.daily_pnl
                if prev_balance > 0:
                    report.balance_change_pct = report.daily_pnl / prev_balance * 100

        # Weekly context
        if self.config.include_weekly_summary and self._get_weekly_trades:
            weekly = self._get_weekly_trades()
            if asyncio.iscoroutine(weekly):
                weekly = await weekly
            if weekly:
                week_stats = TradeStats.from_trades(weekly)
                report.weekly_pnl = week_stats.total_pnl
                report.weekly_trades = week_stats.total_trades
                report.weekly_win_rate = week_stats.win_rate

        log.info(f"[Report] Generated for {report.report_date} | "
                 f"P&L: ${report.daily_pnl:+.2f} | Trades: {report.stats.total_trades}")
        return report

    async def generate_and_deliver(self, target_date: date = None):
        """Generate report and deliver via configured channels."""
        report = await self.generate(target_date)

        # Send Telegram
        if self.config.send_telegram and self._send_telegram:
            message = self.format_telegram(report)
            result = self._send_telegram(message)
            if asyncio.iscoroutine(result):
                await result

        # Save to DB
        if self.config.save_to_db and self._save_to_db:
            result = self._save_to_db(report.to_dict())
            if asyncio.iscoroutine(result):
                await result

        return report

    # ──────────────────────────────────────────
    # Telegram Formatting
    # ──────────────────────────────────────────

    def format_telegram(self, report: DailyReportData) -> str:
        """Format report as Telegram Markdown message."""
        pnl = report.daily_pnl
        pnl_emoji = "📈" if pnl > 0 else "📉" if pnl < 0 else "➡️"

        # Day quality
        if pnl >= self.config.good_day_threshold:
            day_badge = "🟢 GREAT DAY"
        elif pnl > 0:
            day_badge = "🟡 POSITIVE DAY"
        elif pnl == 0:
            day_badge = "⚪ FLAT DAY"
        elif pnl > self.config.bad_day_threshold:
            day_badge = "🟠 MINOR LOSS"
        else:
            day_badge = "🔴 TOUGH DAY"

        msg = (
            f"📋 *DAILY REPORT — {report.report_date}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{day_badge}\n\n"
        )

        # P&L Section
        msg += (
            f"{pnl_emoji} *P&L Summary:*\n"
            f"  Day P&L: *${pnl:+.2f}* ({report.balance_change_pct:+.2f}%)\n"
            f"  Balance: `${report.balance:,.2f}`\n"
            f"  Equity: `${report.equity:,.2f}`\n"
            f"  Drawdown: `{report.drawdown_pct:.2f}%`\n\n"
        )

        # Trade Stats
        msg += (
            f"📊 *Trade Statistics:*\n"
            f"  Trades: `{report.stats.total_trades}`\n"
            f"  Wins: `{report.stats.wins}` | Losses: `{report.stats.losses}`\n"
            f"  Win Rate: `{report.stats.win_rate:.1f}%`\n"
            f"  Profit Factor: `{report.stats.profit_factor:.2f}`\n"
            f"  Avg Win: `${report.stats.avg_win:.2f}`\n"
            f"  Avg Loss: `${report.stats.avg_loss:.2f}`\n\n"
        )

        # Best/Worst
        if self.config.include_best_worst:
            if report.best_trade:
                msg += (
                    f"🏆 *Best Trade:*\n"
                    f"  {report.best_trade.get('direction', '?')} "
                    f"{report.best_trade.get('symbol', 'XAUUSD')} → "
                    f"*${report.best_trade.get('pnl_usd', 0):+.2f}*\n"
                )
            if report.worst_trade:
                msg += (
                    f"💔 *Worst Trade:*\n"
                    f"  {report.worst_trade.get('direction', '?')} "
                    f"{report.worst_trade.get('symbol', 'XAUUSD')} → "
                    f"*${report.worst_trade.get('pnl_usd', 0):+.2f}*\n"
                )
            msg += "\n"

        # Per-pair breakdown
        if self.config.include_per_pair and report.per_pair_stats:
            msg += "📈 *Per Pair:*\n"
            for pair, stats in report.per_pair_stats.items():
                pair_emoji = "🟢" if stats.total_pnl >= 0 else "🔴"
                msg += (
                    f"  {pair_emoji} {pair}: ${stats.total_pnl:+.2f} "
                    f"({stats.wins}W/{stats.losses}L)\n"
                )
            msg += "\n"

        # Risk metrics
        if self.config.include_risk_metrics:
            msg += (
                f"🛡️ *Risk Metrics:*\n"
                f"  Max Risk Used: `{report.max_risk_used_pct:.1f}%`\n"
                f"  Max Drawdown: `{report.max_drawdown_today:.2f}%`\n"
            )
            if report.circuit_breaker_triggered:
                msg += f"  ⚠️ Circuit breaker triggered today\n"
            msg += "\n"

        # Weekly context
        if self.config.include_weekly_summary and report.weekly_trades > 0:
            msg += (
                f"📅 *Week-to-Date:*\n"
                f"  P&L: `${report.weekly_pnl:+.2f}`\n"
                f"  Trades: `{report.weekly_trades}` | "
                f"WR: `{report.weekly_win_rate:.1f}%`\n\n"
            )

        # Open positions
        if self.config.include_open_positions and report.open_positions > 0:
            msg += f"📌 *Open Positions:* `{report.open_positions}`\n\n"

        msg += (
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"_Chastiefol Agent v3.0 | XAUUSD + BTCUSD | Auto-generated_"
        )

        return msg

    def format_weekly(self, daily_reports: List[DailyReportData]) -> str:
        """Format a weekly summary from multiple daily reports."""
        if not daily_reports:
            return "📅 *WEEKLY REPORT*\nNo data available."

        total_pnl = sum(r.daily_pnl for r in daily_reports)
        total_trades = sum(r.stats.total_trades for r in daily_reports)
        total_wins = sum(r.stats.wins for r in daily_reports)
        total_losses = sum(r.stats.losses for r in daily_reports)
        win_rate = total_wins / max(total_trades, 1) * 100
        green_days = sum(1 for r in daily_reports if r.daily_pnl > 0)
        red_days = sum(1 for r in daily_reports if r.daily_pnl < 0)

        start_date = daily_reports[0].report_date
        end_date = daily_reports[-1].report_date

        pnl_emoji = "📈" if total_pnl > 0 else "📉"

        msg = (
            f"📅 *WEEKLY REPORT*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Period: {start_date} → {end_date}\n\n"
            f"{pnl_emoji} *Week P&L: ${total_pnl:+.2f}*\n\n"
            f"📊 *Statistics:*\n"
            f"  Total Trades: `{total_trades}`\n"
            f"  Win Rate: `{win_rate:.1f}%`\n"
            f"  Green Days: `{green_days}` | Red Days: `{red_days}`\n\n"
            f"📆 *Daily Breakdown:*\n"
        )

        for r in daily_reports:
            day_emoji = "🟢" if r.daily_pnl > 0 else "🔴" if r.daily_pnl < 0 else "⚪"
            day_name = r.report_date.strftime("%a")
            msg += f"  {day_emoji} {day_name}: ${r.daily_pnl:+.2f} ({r.stats.total_trades}T)\n"

        msg += (
            f"\n━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"_Chastiefol Agent v3.0 | XAUUSD + BTCUSD | Weekly Summary_"
        )
        return msg


# ──────────────────────────────────────────────
# Report Scheduler
# ──────────────────────────────────────────────

class ReportScheduler:
    """
    Schedules automatic report generation at configured times.

    Usage:
        scheduler = ReportScheduler(generator, config)
        await scheduler.start()
        # ... runs in background ...
        await scheduler.stop()
    """

    def __init__(self, generator: DailyReportGenerator, config: ReportConfig = None):
        self.generator = generator
        self.config = config or ReportConfig()
        self._task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self):
        """Start the scheduler background loop."""
        self._running = True
        self._task = asyncio.create_task(self._scheduler_loop())
        log.info(f"Report scheduler started | "
                 f"Daily: {self.config.daily_report_time_utc} UTC | "
                 f"Weekly: Day {self.config.weekly_report_day} at {self.config.weekly_report_time_utc} UTC")

    async def stop(self):
        """Stop the scheduler."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        log.info("Report scheduler stopped.")

    async def _scheduler_loop(self):
        """Main scheduler loop — checks every 60 seconds."""
        last_daily_date: Optional[date] = None
        last_weekly_date: Optional[date] = None

        while self._running:
            try:
                now = datetime.now(timezone.utc)
                current_time = now.strftime("%H:%M")
                today = now.date()

                # Daily report
                if (current_time == self.config.daily_report_time_utc
                        and last_daily_date != today):
                    log.info("[Scheduler] Triggering daily report...")
                    try:
                        await self.generator.generate_and_deliver(today)
                        last_daily_date = today
                    except Exception as e:
                        log.error(f"[Scheduler] Daily report failed: {e}")

                # Weekly report (on configured day)
                if (now.weekday() == self.config.weekly_report_day
                        and current_time == self.config.weekly_report_time_utc
                        and last_weekly_date != today):
                    log.info("[Scheduler] Triggering weekly report...")
                    # Weekly report would need last 5 daily reports
                    last_weekly_date = today

                await asyncio.sleep(60)  # Check every minute

            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"[Scheduler] Error: {e}")
                await asyncio.sleep(60)

    async def trigger_now(self, report_type: str = "daily") -> Optional[DailyReportData]:
        """Manually trigger a report right now."""
        if report_type == "daily":
            return await self.generator.generate_and_deliver()
        return None


# ──────────────────────────────────────────────
# Standalone Test
# ──────────────────────────────────────────────

if __name__ == "__main__":
    # Simulate report generation
    sample_trades = [
        {"symbol": "XAUUSD", "direction": "BUY", "pnl_usd": 85.0, "outcome": "WIN"},
        {"symbol": "XAUUSD", "direction": "SELL", "pnl_usd": -32.0, "outcome": "LOSS"},
        {"symbol": "XAUUSD", "direction": "BUY", "pnl_usd": 120.5, "outcome": "WIN"},
        {"symbol": "BTCUSD", "direction": "BUY", "pnl_usd": 45.0, "outcome": "WIN"},
        {"symbol": "BTCUSD", "direction": "SELL", "pnl_usd": -28.0, "outcome": "LOSS"},
    ]

    config = ReportConfig()
    generator = DailyReportGenerator(config)
    generator.set_data_sources(
        get_trades=lambda: sample_trades,
        get_account=lambda: {
            "balance": 10186.50,
            "equity": 10186.50,
            "peak_balance": 10200.0,
            "drawdown_pct": 0.13,
            "open_trades": 0,
            "total_risk_pct": 0,
            "max_drawdown_pct": 1.2,
        },
        get_weekly_trades=lambda: sample_trades * 3,
    )

    async def test():
        report = await generator.generate()
        message = generator.format_telegram(report)
        print(message)
        print(f"\n\nReport dict: {report.to_dict()}")

    asyncio.run(test())
