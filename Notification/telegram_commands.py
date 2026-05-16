"""
Chastiefol — Telegram Bot Command Handler
Interactive menu commands for monitoring the trading agent via Telegram.

Commands:
  /start, /help  — Show available commands
  /status        — Agent status (running, mode, components)
  /balance       — Account balance, equity, margin, drawdown
  /positions     — Open positions with P&L
  /trades        — Recent closed trades
  /signals       — Recent signals generated
  /performance   — Win rate, profit factor, summary
  /risk          — Risk metrics (drawdown, daily loss, exposure)
  /ping          — Check if agent is alive (latency test)

Setup:
  1. Create bot via @BotFather
  2. Set commands: /setcommands → paste command list
  3. Configure TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env
  4. Agent auto-starts the command listener on boot
"""

import logging
import asyncio
import time
from datetime import datetime, timezone
from typing import Optional, Callable, Dict, Any

import aiohttp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("Telegram.Commands")


class TelegramCommandHandler:
    """
    Handles incoming Telegram bot commands via long-polling.
    Provides interactive menu for monitoring Chastiefol agent.

    Usage:
        handler = TelegramCommandHandler(bot_token, chat_id)
        handler.set_data_sources(
            get_status=lambda: {...},
            get_balance=lambda: {...},
            get_positions=lambda: [...],
            get_trades=lambda: [...],
            get_signals=lambda: [...],
        )
        await handler.start()
    """

    BASE_URL = "https://api.telegram.org/bot{token}"

    def __init__(self, bot_token: str, allowed_chat_ids: list = None):
        self.bot_token = bot_token
        self.allowed_chat_ids = allowed_chat_ids or []
        self._session: Optional[aiohttp.ClientSession] = None
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._last_update_id = 0
        self._start_time = time.time()

        # Data source callbacks (injected by main orchestrator)
        self._get_status: Optional[Callable] = None
        self._get_balance: Optional[Callable] = None
        self._get_positions: Optional[Callable] = None
        self._get_trades: Optional[Callable] = None
        self._get_signals: Optional[Callable] = None
        self._get_performance: Optional[Callable] = None
        self._get_risk: Optional[Callable] = None

    def set_data_sources(self, get_status=None, get_balance=None,
                         get_positions=None, get_trades=None,
                         get_signals=None, get_performance=None,
                         get_risk=None):
        """Inject data source callbacks from the main agent."""
        self._get_status = get_status
        self._get_balance = get_balance
        self._get_positions = get_positions
        self._get_trades = get_trades
        self._get_signals = get_signals
        self._get_performance = get_performance
        self._get_risk = get_risk

    async def start(self):
        """Start polling for commands."""
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=35)
        )
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        log.info("Telegram command handler started (long-polling).")

    async def stop(self):
        """Stop polling."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._session:
            await self._session.close()
        log.info("Telegram command handler stopped.")

    # ──────────────────────────────────────────
    # Polling Loop
    # ──────────────────────────────────────────

    async def _poll_loop(self):
        """Long-polling loop for incoming messages."""
        while self._running:
            try:
                updates = await self._get_updates()
                for update in updates:
                    await self._handle_update(update)
            except asyncio.CancelledError:
                break
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                log.error(f"Command poll error: {e}")
                await asyncio.sleep(5)

    async def _get_updates(self) -> list:
        """Fetch new updates from Telegram."""
        url = f"{self.BASE_URL.format(token=self.bot_token)}/getUpdates"
        params = {
            "offset": self._last_update_id + 1,
            "timeout": 30,
            "allowed_updates": '["message"]',
        }
        async with self._session.get(url, params=params) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("result", [])
        return []

    async def _handle_update(self, update: dict):
        """Process a single update."""
        self._last_update_id = update.get("update_id", self._last_update_id)
        message = update.get("message", {})
        text = message.get("text", "").strip()
        chat_id = str(message.get("chat", {}).get("id", ""))

        if not text or not text.startswith("/"):
            return

        # Security: only respond to allowed chat IDs
        if self.allowed_chat_ids and chat_id not in self.allowed_chat_ids:
            log.warning(f"Unauthorized command from chat_id: {chat_id}")
            return

        command = text.split()[0].lower().split("@")[0]  # Handle /command@botname
        log.info(f"Command received: {command} from {chat_id}")

        # Route command
        handlers = {
            "/start": self._cmd_help,
            "/help": self._cmd_help,
            "/status": self._cmd_status,
            "/balance": self._cmd_balance,
            "/positions": self._cmd_positions,
            "/trades": self._cmd_trades,
            "/signals": self._cmd_signals,
            "/performance": self._cmd_performance,
            "/risk": self._cmd_risk,
            "/pairs": self._cmd_pairs,
            "/ping": self._cmd_ping,
        }

        handler = handlers.get(command)
        if handler:
            response = await handler()
            await self._send_message(chat_id, response)
        else:
            await self._send_message(chat_id,
                "❓ Unknown command. Type /help for available commands.")

    # ──────────────────────────────────────────
    # Command Handlers
    # ──────────────────────────────────────────

    async def _cmd_help(self) -> str:
        """Show available commands."""
        return (
            "⚔️ *CHASTIEFOL — Command Menu*\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "📊 `/status` — Agent status & components\n"
            "💰 `/balance` — Account balance & equity\n"
            "📈 `/positions` — Open positions & P&L\n"
            "📋 `/trades` — Recent closed trades\n"
            "🔔 `/signals` — Recent signals generated\n"
            "🏆 `/performance` — Win rate & statistics\n"
            "🛡️ `/risk` — Risk metrics & drawdown\n"
            "🪙 `/pairs` — Daftar crypto yang di-tradingkan\n"
            "🏓 `/ping` — Check agent is alive\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "_Chastiefol Multi-Pair Agent_\n"
            "_XAUUSD | Crypto (100 pairs)_"
        )

    async def _cmd_status(self) -> str:
        """Show agent status."""
        if not self._get_status:
            return "⚠️ Status data not available."

        data = self._get_status()
        if asyncio.iscoroutine(data):
            data = await data

        if not data:
            return "⚠️ Could not retrieve status."

        running = data.get("running", False)
        mode = data.get("mode", "unknown")
        paper = data.get("paper_mode", True)
        symbols = data.get("symbols", data.get("symbol", "XAUUSD, BTCUSD"))
        components = data.get("components", {})

        status_emoji = "🟢" if running else "🔴"
        mode_emoji = "🤖" if mode == "autonomous" else "📡" if mode == "webhook" else "🔀"

        # Component status
        comp_lines = []
        comp_map = {
            "webhook": "Webhook",
            "ctrader_mcp": "cTrader MCP",
            "ctrader_fix": "cTrader FIX",
            "data_feed": "Data Feed",
            "telegram": "Telegram",
        }
        for key, label in comp_map.items():
            is_active = components.get(key, False)
            emoji = "✅" if is_active else "❌"
            comp_lines.append(f"  {emoji} {label}")

        uptime_sec = time.time() - self._start_time
        hours = int(uptime_sec // 3600)
        minutes = int((uptime_sec % 3600) // 60)
        uptime_str = f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"

        return (
            f"{status_emoji} *AGENT STATUS*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Status: *{'ONLINE' if running else 'OFFLINE'}*\n"
            f"{mode_emoji} Mode: `{mode.upper()}`\n"
            f"Pairs: `{symbols}`\n"
            f"Paper: `{'Yes' if paper else 'No — LIVE'}`\n"
            f"Uptime: `{uptime_str}`\n\n"
            f"*Components:*\n"
            f"{chr(10).join(comp_lines)}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🕐 _{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}_"
        )

    async def _cmd_balance(self) -> str:
        """Show account balance."""
        if not self._get_balance:
            return "⚠️ Balance data not available."

        data = self._get_balance()
        if asyncio.iscoroutine(data):
            data = await data

        if not data:
            return "⚠️ Could not retrieve balance."

        balance = data.get("balance", 0)
        equity = data.get("equity", balance)
        margin_used = data.get("margin_used", 0)
        free_margin = data.get("free_margin", equity)
        leverage = data.get("leverage", 100)
        unrealized_pnl = data.get("unrealized_pnl", 0)
        drawdown_pct = data.get("drawdown_pct", 0)
        daily_pnl = data.get("daily_pnl", 0)

        pnl_emoji = "📈" if daily_pnl >= 0 else "📉"
        dd_emoji = "🟢" if drawdown_pct < 3 else "🟡" if drawdown_pct < 7 else "🔴"

        return (
            f"💰 *ACCOUNT BALANCE*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"💵 Balance: `${balance:,.2f}`\n"
            f"📊 Equity: `${equity:,.2f}`\n"
            f"🔒 Margin Used: `${margin_used:,.2f}`\n"
            f"✅ Free Margin: `${free_margin:,.2f}`\n"
            f"⚖️ Leverage: `1:{leverage}`\n\n"
            f"{pnl_emoji} Daily P&L: *${daily_pnl:+,.2f}*\n"
            f"💎 Unrealized: `${unrealized_pnl:+,.2f}`\n"
            f"{dd_emoji} Drawdown: `{drawdown_pct:.2f}%`\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🕐 _{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}_"
        )

    async def _cmd_positions(self) -> str:
        """Show open positions."""
        if not self._get_positions:
            return "⚠️ Position data not available."

        data = self._get_positions()
        if asyncio.iscoroutine(data):
            data = await data

        if not data:
            return "📭 *No open positions.*"

        msg = "📈 *OPEN POSITIONS*\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        total_pnl = 0.0
        for i, pos in enumerate(data[:10], 1):
            direction = pos.get("direction", pos.get("side", "?"))
            symbol = pos.get("symbol", "XAUUSD")
            entry = pos.get("entry_price", pos.get("entry", 0))
            current = pos.get("current_price", 0)
            lot = pos.get("lot_size", pos.get("volume", 0))
            pnl = pos.get("pnl", pos.get("pnl_usd", 0))
            sl = pos.get("stop_loss", 0)
            tp = pos.get("take_profit", 0)

            total_pnl += pnl
            dir_emoji = "🟢" if direction.upper() == "BUY" else "🔴"
            pnl_emoji = "💚" if pnl >= 0 else "💔"

            msg += (
                f"{dir_emoji} *{direction} {symbol}* — {lot} lots\n"
                f"  Entry: `${entry:.2f}` → Now: `${current:.2f}`\n"
                f"  SL: `${sl:.2f}` | TP: `${tp:.2f}`\n"
                f"  {pnl_emoji} P&L: *${pnl:+.2f}*\n\n"
            )

        total_emoji = "📈" if total_pnl >= 0 else "📉"
        msg += (
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{total_emoji} Total Unrealized: *${total_pnl:+.2f}*\n"
            f"Open: `{len(data)}` position(s)"
        )
        return msg

    async def _cmd_trades(self) -> str:
        """Show recent closed trades."""
        if not self._get_trades:
            return "⚠️ Trade data not available."

        data = self._get_trades()
        if asyncio.iscoroutine(data):
            data = await data

        if not data:
            return "📭 *No trades yet.*"

        msg = "📋 *RECENT TRADES*\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        for trade in data[:8]:
            direction = trade.get("direction", "?")
            pnl = trade.get("pnl_usd", 0)
            outcome = trade.get("outcome", "?")
            entry = trade.get("entry", trade.get("entry_price", 0))
            exit_p = trade.get("exit", trade.get("exit_price", 0))
            lot = trade.get("lot_size", 0)

            emoji = "✅" if outcome == "WIN" else "❌"
            msg += (
                f"{emoji} {direction} `${entry:.2f}`→`${exit_p:.2f}` "
                f"({lot} lots) *${pnl:+.2f}*\n"
            )

        total = sum(t.get("pnl_usd", 0) for t in data[:8])
        msg += (
            f"\n━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Sum (last {min(len(data), 8)}): *${total:+.2f}*"
        )
        return msg

    async def _cmd_signals(self) -> str:
        """Show recent signals."""
        if not self._get_signals:
            return "⚠️ Signal data not available."

        data = self._get_signals()
        if asyncio.iscoroutine(data):
            data = await data

        if not data:
            return "📭 *No signals generated yet.*"

        msg = "🔔 *RECENT SIGNALS*\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        for sig in data[:6]:
            action = sig.get("action", sig.get("signal", "?"))
            symbol = sig.get("symbol", "—")
            confidence = sig.get("confidence", 0)
            confluence = sig.get("confluence", 0)
            timestamp = sig.get("timestamp", sig.get("created_at", ""))

            emoji = "🟢" if action == "BUY" else "🔴" if action == "SELL" else "⚪"
            time_str = ""
            if timestamp:
                try:
                    dt = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
                    time_str = dt.strftime("%H:%M")
                except Exception:
                    time_str = str(timestamp)[:5]

            msg += (
                f"{emoji} *{action}* {symbol} "
                f"| Conf: `{confluence}/7` "
                f"| {time_str}\n"
            )

        msg += f"\n━━━━━━━━━━━━━━━━━━━━━━━━\nTotal: {len(data)} signals"
        return msg

    async def _cmd_performance(self) -> str:
        """Show trading performance stats."""
        if not self._get_performance:
            return "⚠️ Performance data not available."

        data = self._get_performance()
        if asyncio.iscoroutine(data):
            data = await data

        if not data:
            return "📭 *No performance data yet.*"

        total = data.get("total", data.get("total_trades", 0))
        wins = data.get("wins", 0)
        losses = data.get("losses", 0)
        win_rate = data.get("win_rate", 0)
        pf = data.get("profit_factor", 0)
        total_pnl = data.get("total_pnl", 0)
        avg_win = data.get("avg_win", 0)
        avg_loss = data.get("avg_loss", 0)
        signals = data.get("signals_generated", 0)

        trophy = "🏆" if win_rate >= 60 else "📊" if win_rate >= 50 else "⚠️"

        return (
            f"{trophy} *PERFORMANCE*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📊 Total Trades: `{total}`\n"
            f"✅ Wins: `{wins}` | ❌ Losses: `{losses}`\n"
            f"🎯 Win Rate: *{win_rate:.1f}%*\n"
            f"⚖️ Profit Factor: `{pf:.2f}`\n\n"
            f"💰 Total P&L: *${total_pnl:+,.2f}*\n"
            f"📈 Avg Win: `${avg_win:.2f}`\n"
            f"📉 Avg Loss: `${avg_loss:.2f}`\n"
            f"🔔 Signals Generated: `{signals}`\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━"
        )

    async def _cmd_risk(self) -> str:
        """Show risk metrics."""
        if not self._get_risk:
            return "⚠️ Risk data not available."

        data = self._get_risk()
        if asyncio.iscoroutine(data):
            data = await data

        if not data:
            return "⚠️ Could not retrieve risk data."

        drawdown = data.get("drawdown_pct", data.get("current_drawdown_pct", 0))
        max_dd = data.get("max_drawdown_pct", 0)
        daily_loss = data.get("daily_loss_pct", 0)
        max_daily = data.get("max_daily_loss_pct", 3.0)
        open_risk = data.get("total_risk_pct", 0)
        max_risk = data.get("max_total_risk_pct", 5.0)
        open_trades = data.get("open_trades", 0)
        max_trades = data.get("max_open_trades", 2)
        trading_allowed = data.get("trading_allowed", True)

        dd_emoji = "🟢" if drawdown < 3 else "🟡" if drawdown < 7 else "🔴"
        status_emoji = "✅" if trading_allowed else "🛑"

        return (
            f"🛡️ *RISK METRICS*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{status_emoji} Trading: *{'ALLOWED' if trading_allowed else 'HALTED'}*\n\n"
            f"{dd_emoji} Current Drawdown: `{drawdown:.2f}%` / `{max_dd:.1f}%`\n"
            f"📉 Daily Loss: `{daily_loss:.2f}%` / `{max_daily:.1f}%`\n"
            f"⚖️ Open Risk: `{open_risk:.1f}%` / `{max_risk:.1f}%`\n"
            f"📊 Open Trades: `{open_trades}` / `{max_trades}`\n\n"
            f"*Thresholds:*\n"
            f"  Circuit Breaker: `{max_dd:.0f}%` drawdown\n"
            f"  Daily Stop: `{max_daily:.0f}%` loss\n"
            f"  Max Positions: `{max_trades}`\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━"
        )

    async def _cmd_pairs(self) -> str:
        """Show list of tradeable crypto pairs."""
        try:
            from Analysis.crypto_pair_config import (
                CRYPTO_CONFIGS, get_pairs_by_category, CryptoCategory
            )

            total = len(CRYPTO_CONFIGS)

            # Count by category
            categories = {}
            for cfg in CRYPTO_CONFIGS.values():
                cat = cfg.category.value if hasattr(cfg.category, 'value') else str(cfg.category)
                categories[cat] = categories.get(cat, 0) + 1

            # Build category lines
            cat_emojis = {
                "large_cap": "🏦", "mid_cap": "📊", "small_cap": "🔹",
                "meme": "🐸", "defi": "🏗️", "ai": "🤖",
                "layer2": "⚡", "gaming": "🎮", "infrastructure": "🔗",
            }

            cat_lines = []
            for cat, count in sorted(categories.items(), key=lambda x: -x[1]):
                emoji = cat_emojis.get(cat, "•")
                # Sample pairs for each category
                sample = [c.symbol.split("/")[0] for c in CRYPTO_CONFIGS.values()
                          if (c.category.value if hasattr(c.category, 'value') else "") == cat][:6]
                sample_str = ", ".join(sample)
                if len([c for c in CRYPTO_CONFIGS.values()
                       if (c.category.value if hasattr(c.category, 'value') else "") == cat]) > 6:
                    sample_str += "..."
                cat_lines.append(f"{emoji} *{cat.replace('_', ' ').title()}* ({count})\n    `{sample_str}`")

            msg = (
                f"🪙 *DAFTAR CRYPTO — {total} Pairs*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Exchange: *Binance* (Spot + Futures)\n"
                f"Quote: *USDT*\n\n"
            )
            msg += "\n".join(cat_lines)
            msg += (
                f"\n\n━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📡 Data: CCXT + Binance WebSocket\n"
                f"🧠 Learning: {total} pairs tracked\n"
                f"_Gunakan tier: top10, top20, defi, ai, meme, all_"
            )
            return msg

        except ImportError:
            return "⚠️ Crypto pair config not available."
        except Exception as e:
            return f"⚠️ Error loading pairs: {e}"

    async def _cmd_ping(self) -> str:
        """Simple alive check with latency."""
        latency_ms = round((time.time() - self._start_time) % 1 * 1000, 1)
        uptime_sec = time.time() - self._start_time
        hours = int(uptime_sec // 3600)
        minutes = int((uptime_sec % 3600) // 60)

        return (
            f"🏓 *PONG!*\n\n"
            f"Agent is alive.\n"
            f"Uptime: `{hours}h {minutes}m`\n"
            f"Time: `{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}`"
        )

    # ──────────────────────────────────────────
    # Send Message
    # ──────────────────────────────────────────

    async def _send_message(self, chat_id: str, text: str):
        """Send a message to a chat."""
        url = f"{self.BASE_URL.format(token=self.bot_token)}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_notification": False,
        }
        try:
            async with self._session.post(url, json=payload) as resp:
                if resp.status != 200:
                    data = await resp.json()
                    log.error(f"Send failed: {data.get('description', '')}")
        except Exception as e:
            log.error(f"Send message error: {e}")
