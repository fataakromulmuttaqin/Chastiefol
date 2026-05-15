"""
Chastiefol — Dashboard REST API
Serves real-time trading data to the React frontend.
Endpoints: /api/status, /api/trades, /api/equity, /api/signals, /api/portfolio
"""

import json
import logging
import asyncio
from datetime import datetime, timezone
from typing import Optional
from aiohttp import web

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("Dashboard.API")


class DashboardAPI:
    """
    REST API server for the Chastiefol React dashboard.
    Integrates with the main agent to serve live data.
    """

    def __init__(self, host: str = "0.0.0.0", port: int = 3001):
        self.host = host
        self.port = port
        self._app: Optional[web.Application] = None
        self._runner: Optional[web.AppRunner] = None

        # Data sources (injected by main orchestrator)
        self._get_status = None
        self._get_trades = None
        self._get_equity_curve = None
        self._get_signals = None
        self._get_portfolio = None

    def set_data_sources(self, status_fn=None, trades_fn=None,
                         equity_fn=None, signals_fn=None, portfolio_fn=None):
        """Inject data source callbacks from the main agent."""
        self._get_status = status_fn
        self._get_trades = trades_fn
        self._get_equity_curve = equity_fn
        self._get_signals = signals_fn
        self._get_portfolio = portfolio_fn

    async def start(self):
        """Start the API server."""
        self._app = web.Application(middlewares=[self._cors_middleware])
        self._register_routes()

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.host, self.port)
        await site.start()
        log.info(f"Dashboard API started on http://{self.host}:{self.port}")

    async def stop(self):
        if self._runner:
            await self._runner.cleanup()

    def _register_routes(self):
        self._app.router.add_get("/api/status", self._handle_status)
        self._app.router.add_get("/api/trades", self._handle_trades)
        self._app.router.add_get("/api/equity", self._handle_equity)
        self._app.router.add_get("/api/signals", self._handle_signals)
        self._app.router.add_get("/api/portfolio", self._handle_portfolio)
        self._app.router.add_get("/api/health", self._handle_health)

    @web.middleware
    async def _cors_middleware(self, request, handler):
        if request.method == "OPTIONS":
            response = web.Response()
        else:
            response = await handler(request)
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return response

    async def _handle_health(self, request):
        return web.json_response({"status": "ok", "service": "dashboard-api"})

    async def _handle_status(self, request):
        data = self._get_status() if self._get_status else {
            "running": True, "mode": "paper", "balance": 10000,
            "equity": 10000, "open_trades": 0, "daily_pnl": 0,
        }
        return web.json_response(data)

    async def _handle_trades(self, request):
        limit = int(request.query.get("limit", 50))
        data = self._get_trades(limit) if self._get_trades else []
        return web.json_response({"trades": data, "total": len(data)})

    async def _handle_equity(self, request):
        data = self._get_equity_curve() if self._get_equity_curve else []
        return web.json_response({"curve": data})

    async def _handle_signals(self, request):
        limit = int(request.query.get("limit", 20))
        data = self._get_signals(limit) if self._get_signals else []
        return web.json_response({"signals": data})

    async def _handle_portfolio(self, request):
        data = self._get_portfolio() if self._get_portfolio else {}
        return web.json_response(data)
