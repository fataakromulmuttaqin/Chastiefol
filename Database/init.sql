-- ═══════════════════════════════════════════════════════════════
-- CHASTIEFOL — PostgreSQL Initial Schema
-- Auto-executed on first docker-compose up
-- ═══════════════════════════════════════════════════════════════

-- Trades table
CREATE TABLE IF NOT EXISTS trades (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL DEFAULT 'XAUUSD',
    direction VARCHAR(10) NOT NULL,
    entry_price DECIMAL(12, 5) NOT NULL,
    exit_price DECIMAL(12, 5),
    stop_loss DECIMAL(12, 5),
    take_profit DECIMAL(12, 5),
    lot_size DECIMAL(8, 4) NOT NULL,
    pnl_usd DECIMAL(12, 2),
    outcome VARCHAR(10),
    confidence DECIMAL(5, 4),
    confluence INTEGER,
    comment TEXT,
    source VARCHAR(20) DEFAULT 'autonomous',
    opened_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Signals table
CREATE TABLE IF NOT EXISTS signals (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL DEFAULT 'XAUUSD',
    action VARCHAR(10) NOT NULL,
    entry_price DECIMAL(12, 5),
    sl_pips DECIMAL(10, 2),
    tp_pips DECIMAL(10, 2),
    confluence INTEGER,
    confidence DECIMAL(5, 4),
    source VARCHAR(20) DEFAULT 'webhook',
    executed BOOLEAN DEFAULT FALSE,
    raw_payload JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Account snapshots (for equity curve)
CREATE TABLE IF NOT EXISTS account_snapshots (
    id SERIAL PRIMARY KEY,
    balance DECIMAL(12, 2) NOT NULL,
    equity DECIMAL(12, 2) NOT NULL,
    open_trades INTEGER DEFAULT 0,
    daily_pnl DECIMAL(12, 2) DEFAULT 0,
    drawdown_pct DECIMAL(5, 2) DEFAULT 0,
    snapshot_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Daily reports
CREATE TABLE IF NOT EXISTS daily_reports (
    id SERIAL PRIMARY KEY,
    report_date DATE NOT NULL UNIQUE,
    total_trades INTEGER DEFAULT 0,
    wins INTEGER DEFAULT 0,
    losses INTEGER DEFAULT 0,
    total_pnl DECIMAL(12, 2) DEFAULT 0,
    win_rate DECIMAL(5, 2) DEFAULT 0,
    max_drawdown DECIMAL(5, 2) DEFAULT 0,
    balance DECIMAL(12, 2),
    report_json JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Indexes
CREATE INDEX idx_trades_symbol ON trades(symbol);
CREATE INDEX idx_trades_opened_at ON trades(opened_at);
CREATE INDEX idx_trades_outcome ON trades(outcome);
CREATE INDEX idx_signals_created_at ON signals(created_at);
CREATE INDEX idx_signals_action ON signals(action);
CREATE INDEX idx_account_snapshots_at ON account_snapshots(snapshot_at);
CREATE INDEX idx_daily_reports_date ON daily_reports(report_date);
