import React from 'react'

function StatCard({ label, value, sub, color }) {
  return (
    <div className="card">
      <p className="stat-label">{label}</p>
      <p className={`stat-value ${color || 'text-white'}`}>{value}</p>
      {sub && <p className="text-xs text-gray-500 mt-1">{sub}</p>}
    </div>
  )
}

export default function StatusCards({ status, loading }) {
  if (loading || !status) {
    return (
      <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-4">
        {[...Array(6)].map((_, i) => (
          <div key={i} className="card animate-pulse h-24" />
        ))}
      </div>
    )
  }

  const balance = status.balance || 0
  const equity = status.equity || balance
  const dailyPnl = status.daily_pnl || 0
  const openTrades = status.open_trades || status.open_positions || 0
  const totalTrades = status.trades_completed || status.total_trades || 0
  const drawdown = status.drawdown_pct || 0

  return (
    <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
      <StatCard
        label="Balance"
        value={`$${balance.toLocaleString(undefined, { minimumFractionDigits: 2 })}`}
      />
      <StatCard
        label="Equity"
        value={`$${equity.toLocaleString(undefined, { minimumFractionDigits: 2 })}`}
      />
      <StatCard
        label="Daily P&L"
        value={`${dailyPnl >= 0 ? '+' : ''}$${dailyPnl.toFixed(2)}`}
        color={dailyPnl >= 0 ? 'text-profit' : 'text-loss'}
      />
      <StatCard
        label="Open Trades"
        value={openTrades}
      />
      <StatCard
        label="Total Trades"
        value={totalTrades}
      />
      <StatCard
        label="Drawdown"
        value={`${drawdown.toFixed(1)}%`}
        color={drawdown > 5 ? 'text-loss' : drawdown > 2 ? 'text-yellow-400' : 'text-profit'}
      />
    </div>
  )
}
