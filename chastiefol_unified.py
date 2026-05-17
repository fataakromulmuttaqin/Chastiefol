"""
Chastiefol — Unified Dual-Platform Trading Bot
Runs BOTH trading platforms simultaneously in a single process:

  Platform 1: XAUUSD → cTrader (MCP / FIX API)
  Platform 2: Crypto (100 pairs) → Binance (CCXT + WebSocket)

Architecture:
  ┌─────────────────────────────────────────────────────────────────┐
  │                    CHASTIEFOL UNIFIED                             │
  ├─────────────────────────────────────────────────────────────────┤
  │                                                                  │
  │  ┌─────────────────────┐      ┌─────────────────────────────┐  │
  │  │  XAUUSD Agent        │      │  Crypto Agent               │  │
  │  │  ─────────────────── │      │  ───────────────────────────│  │
  │  │  Data: TradingView WS│      │  Data: CCXT/Binance + WS    │  │
  │  │  Analysis: SMC engine│      │  Analysis: Crypto engine     │  │
  │  │  Exec: cTrader MCP   │      │  Exec: Binance CCXT         │  │
  │  │  Risk: Gold risk mgr │      │  Risk: Crypto risk mgr      │  │
  │  └─────────────────────┘      └─────────────────────────────┘  │
  │                                                                  │
  │  ┌───────────────────────────────────────────────────────────┐  │
  │  │  Shared: Telegram Notifier | LLM Learning | Dashboard     │  │
  │  └───────────────────────────────────────────────────────────┘  │
  │                                                                  │
  └─────────────────────────────────────────────────────────────────┘

Usage:
    # Run both platforms simultaneously
    python chastiefol_unified.py

    # Environment variables control each platform independently:
    # XAUUSD: AGENT_MODE, SYMBOL, EXECUTION_METHOD, CTRADER_*, FIX_*
    # Crypto: CRYPTO_MODE, CRYPTO_WATCHLIST, BINANCE_*, CRYPTO_*

Requirements:
    - cTrader account (for XAUUSD) — MCP token or FIX credentials
    - Binance account (for crypto) — API key + secret
    - Both can run in PAPER mode independently
"""

import asyncio
import logging
import os
import sys
import signal
from pathlib import Path
from datetime import datetime, timezone

# Add project root to path
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("Chastiefol.Unified")


# ──────────────────────────────────────────────
# Platform Runners
# ──────────────────────────────────────────────

async def run_xauusd_platform():
    """
    Run XAUUSD trading platform (cTrader).
    
    Connection: cTrader MCP Server or FIX API
    Data Feed: TradingView WebSocket (FREE)
    Analysis: SMC + Technical Indicators
    Execution: cTrader broker
    """
    try:
        from chastiefol_main import ChastiefollIntegrated, IntegratedConfig
        
        config = IntegratedConfig.from_env()
        agent = ChastiefollIntegrated(config)
        
        log.info("━━━ XAUUSD Platform Starting (cTrader) ━━━")
        await agent.start()
        
        while True:
            await asyncio.sleep(1)
            
    except asyncio.CancelledError:
        log.info("XAUUSD platform shutting down...")
        if 'agent' in dir():
            await agent.stop()
    except Exception as e:
        log.error(f"XAUUSD platform error: {e}")
        raise


async def run_crypto_platform():
    """
    Run Crypto trading platform (Binance).
    
    Connection: Binance via CCXT library
    Data Feed: CCXT REST + Binance WebSocket
    Analysis: Crypto engine (SMC + Volume + Fibonacci)
    Execution: Binance spot orders
    """
    try:
        from chastiefol_crypto_main import ChastiefollCrypto, CryptoConfig
        
        config = CryptoConfig.from_env()
        bot = ChastiefollCrypto(config)
        
        log.info("━━━ Crypto Platform Starting (Binance) ━━━")
        await bot.start()
        
        while True:
            await asyncio.sleep(1)
            
    except asyncio.CancelledError:
        log.info("Crypto platform shutting down...")
        if 'bot' in dir():
            await bot.stop()
    except Exception as e:
        log.error(f"Crypto platform error: {e}")
        raise


# ──────────────────────────────────────────────
# Unified Orchestrator
# ──────────────────────────────────────────────

async def main():
    """
    Main entry point — runs both platforms in parallel.
    
    Each platform runs independently:
    - If one crashes, the other continues
    - Each has its own risk manager
    - Shared Telegram notifications
    - Shared LLM learning system
    """
    log.info(f"""
{'═'*65}
  ⚔️  CHASTIEFOL UNIFIED TRADING BOT
{'═'*65}
  
  Platform 1: XAUUSD → cTrader (MCP/FIX)
  Platform 2: Crypto → Binance (CCXT + WebSocket)
  
  Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}
  
{'═'*65}
""")

    # Determine which platforms to run
    run_xauusd = os.getenv("ENABLE_XAUUSD", "true").lower() == "true"
    run_crypto = os.getenv("ENABLE_CRYPTO", "true").lower() == "true"

    if not run_xauusd and not run_crypto:
        log.error("Both platforms disabled! Set ENABLE_XAUUSD=true or ENABLE_CRYPTO=true")
        return

    tasks = []

    if run_xauusd:
        log.info("  ✓ XAUUSD platform: ENABLED")
        log.info(f"    Mode: {os.getenv('AGENT_MODE', 'hybrid')}")
        log.info(f"    Execution: {os.getenv('EXECUTION_METHOD', 'paper')}")
        log.info(f"    Paper: {os.getenv('PAPER_MODE', 'true')}")
        tasks.append(asyncio.create_task(run_xauusd_platform()))
    else:
        log.info("  ✗ XAUUSD platform: DISABLED")

    if run_crypto:
        log.info("  ✓ Crypto platform: ENABLED")
        log.info(f"    Mode: {os.getenv('CRYPTO_MODE', 'paper')}")
        log.info(f"    Watchlist: {os.getenv('CRYPTO_WATCHLIST', 'top20')}")
        log.info(f"    Exchange: Binance")
        tasks.append(asyncio.create_task(run_crypto_platform()))
    else:
        log.info("  ✗ Crypto platform: DISABLED")

    log.info(f"\n{'═'*65}")
    log.info(f"  ALL PLATFORMS LAUNCHED — Trading is active")
    log.info(f"{'═'*65}\n")

    # Wait for all tasks (or until interrupted)
    try:
        # gather with return_exceptions=True so one crash doesn't kill both
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                platform = "XAUUSD" if i == 0 and run_xauusd else "Crypto"
                log.error(f"{platform} platform crashed: {result}")
                
    except asyncio.CancelledError:
        log.info("Unified bot interrupted — shutting down all platforms...")
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


# ──────────────────────────────────────────────
# Entry Point
# ──────────────────────────────────────────────

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("\nInterrupted by user. Goodbye!")
