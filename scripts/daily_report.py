#!/usr/bin/env python3
"""
Daily Report Generator for Chastiefol
Reads performance.json, compiles stats for last 24h, sends Telegram report.
"""
import sys
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("DailyReport")

TELEGRAM_TOKEN = "8696817310:AAFWBWLhuN-P64sm8JCWK_OWR1UiSz_ToKU"
TELEGRAM_CHAT_ID = "633709469"
PERF_FILE = Path("/home/fataakromulm/Chastiefol/LLM/data/performance.json")
LESSONS_FILE = Path("/home/fataakromulm/Chastiefol/LLM/data/lessons.json")


def load_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning(f"Could not load {path}: {e}")
        return []


def filter_last_24h(records, date_field="closed_at"):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    filtered = []
    for r in records:
        ts = r.get(date_field, "")
        try:
            # Parse ISO timestamp
            if "+" in ts:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            else:
                dt = datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)
            if dt >= cutoff:
                filtered.append(r)
        except Exception:
            pass
    return filtered


def compute_stats(trades):
    if not trades:
        return dict(
            total_trades=0, wins=0, losses=0, total_pnl=0.0,
            win_rate=0.0, max_drawdown=0.0, per_pair={},
            best_trade=None, worst_trade=None,
        )

    wins = sum(1 for t in trades if t.get("pnl_usd", 0) > 0)
    losses = len(trades) - wins
    total_pnl = sum(t.get("pnl_usd", 0) for t in trades)
    win_rate = (wins / len(trades) * 100) if trades else 0

    # Per-pair breakdown
    per_pair = {}
    for t in trades:
        sym = t.get("symbol", "?")
        if sym not in per_pair:
            per_pair[sym] = {"pnl": 0, "trades": 0, "wins": 0, "losses": 0}
        per_pair[sym]["pnl"] += t.get("pnl_usd", 0)
        per_pair[sym]["trades"] += 1
        if t.get("pnl_usd", 0) > 0:
            per_pair[sym]["wins"] += 1
        else:
            per_pair[sym]["losses"] += 1

    # Best/worst
    sorted_trades = sorted(trades, key=lambda x: x.get("pnl_usd", 0), reverse=True)
    best = sorted_trades[0] if sorted_trades else None
    worst = sorted_trades[-1] if sorted_trades else None

    return dict(
        total_trades=len(trades),
        wins=wins, losses=losses,
        total_pnl=total_pnl,
        win_rate=win_rate,
        max_drawdown=0.0,  # placeholder — requires equity curve
        per_pair=per_pair,
        best_trade=best,
        worst_trade=worst,
    )


def count_lessons_today(lessons):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    count = 0
    for l in lessons:
        ts = l.get("created_at", l.get("timestamp", ""))
        try:
            if ts:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if dt >= cutoff:
                    count += 1
        except Exception:
            pass
    return count


async def send_telegram(text: str):
    """Best-effort Telegram delivery for the daily report cron.

    Uses an explicit timeout so a slow/blocked Telegram API can't hang the
    cron job indefinitely, and logs failures so missed reports are visible
    instead of being silently dropped.
    """
    import aiohttp
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}
    timeout = aiohttp.ClientTimeout(total=15)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    print(
                        f"[daily_report] Telegram returned HTTP {resp.status}: "
                        f"{body[:200]}"
                    )
    except Exception as exc:  # network error, timeout, dns, etc.
        print(f"[daily_report] Telegram send failed ({type(exc).__name__}): {exc}")


def build_report(stats, lessons_today, date_str):
    total = stats["total_trades"]
    wins = stats["wins"]
    losses = stats["losses"]
    total_pnl = stats["total_pnl"]
    win_rate = stats["win_rate"]
    per_pair = stats["per_pair"]
    best = stats.get("best_trade")
    worst = stats.get("worst_trade")
    balance = 1000.0  # fallback — real balance requires FIX query

    pnl_emoji = "📈" if total_pnl >= 0 else "📉"
    if total > 0:
        if win_rate >= 70: day_grade = "🏆 A+"
        elif win_rate >= 60: day_grade = "⭐ A"
        elif win_rate >= 50: day_grade = "👍 B"
        elif win_rate >= 40: day_grade = "⚠️ C"
        else: day_grade = "📉 D"
    else:
        day_grade = "➖ No trades"

    msg = (
        f"📋 *DAILY REPORT — {date_str}*\n"
        f"╔══════════════════════════╗\n"
        f"║  {pnl_emoji} P&L: *${total_pnl:+,.2f}*  {day_grade}\n"
        f"╚══════════════════════════╝\n\n"
        f"┌─ *PERFORMANCE* ────────────┐\n"
        f"│ 📊 Trades:    `{total}`\n"
        f"│ ✅ Wins:      `{wins}`\n"
        f"│ ❌ Losses:    `{losses}`\n"
        f"│ 🏆 Win Rate:  `{win_rate:.1f}%`\n"
        f"└────────────────────────────┘\n\n"
        f"┌─ *ACCOUNT* ────────────────┐\n"
        f"│ 💼 Balance:    `$1,000.00`\n"
        f"│ 📊 Equity:     `$1,000.00`\n"
        f"│ 📉 Max DD:     `0.00%`\n"
        f"│ {pnl_emoji} Day P&L:   *${total_pnl:+,.2f}*\n"
        f"└────────────────────────────┘\n"
    )

    if per_pair:
        msg += f"\n┌─ *PER PAIR* ───────────────┐\n"
        for pair, s in sorted(per_pair.items(), key=lambda x: x[1]["pnl"], reverse=True):
            pe = "🟢" if s["pnl"] >= 0 else "🔴"
            msg += f"│ {pe} {pair}: `${s['pnl']:+,.2f}` ({s['wins']}W/{s['losses']}L)\n"
        msg += f"└────────────────────────────┘\n"

    if best:
        msg += f"\n┌─ *HIGHLIGHTS* ─────────────┐\n"
        msg += f"│ 🏆 Best: {best.get('symbol','?')} `+${best.get('pnl_usd',0):,.2f}`\n"
    if worst:
        msg += f"│ 💸 Worst: {worst.get('symbol','?')} `${worst.get('pnl_usd',0):,.2f}`\n"
    if best or worst:
        msg += f"└────────────────────────────┘\n"

    if lessons_today > 0:
        msg += f"\n🧠 _AI learned {lessons_today} new lesson{'s' if lessons_today > 1 else ''} today._\n"

    msg += f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n_⚔️ Chastiefol Unified v5.0_"
    return msg


async def main():
    now_utc = datetime.now(timezone.utc)
    date_str = now_utc.strftime("%Y-%m-%d")

    log.info(f"Generating daily report for {date_str}...")

    # Load data
    perf = load_json(PERF_FILE)
    lessons = load_json(LESSONS_FILE)

    # Filter last 24h
    trades = filter_last_24h(perf)
    lessons_today = count_lessons_today(lessons) if lessons else 0

    log.info(f"Trades in last 24h: {len(trades)}, Lessons: {lessons_today}")

    stats = compute_stats(trades)
    report = build_report(stats, lessons_today, date_str)

    await send_telegram(report)
    log.info("Daily report sent to Telegram.")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())