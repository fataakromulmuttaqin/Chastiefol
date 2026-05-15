import React from 'react'

export default function PortfolioView({ portfolio }) {
  const pairs = portfolio?.pairs || samplePairs

  return (
    <div className="card h-full">
      <h2 className="text-sm font-semibold text-gray-300 mb-4">Portfolio</h2>
      <div className="space-y-3">
        {Object.entries(pairs).map(([symbol, data]) => (
          <div key={symbol} className="flex items-center justify-between p-2 rounded-lg bg-gray-800/40">
            <div>
              <p className="text-xs font-bold">{symbol}</p>
              <p className="text-[10px] text-gray-500">
                {data.open_trades || 0} open | {data.total_trades || 0} total
              </p>
            </div>
            <div className="text-right">
              <p className={`text-xs font-bold ${
                (data.pnl || 0) >= 0 ? 'text-profit' : 'text-loss'
              }`}>
                {(data.pnl || 0) >= 0 ? '+' : ''}${(data.pnl || 0).toFixed(2)}
              </p>
              <p className="text-[10px] text-gray-500">
                WR: {(data.win_rate || 0).toFixed(0)}%
              </p>
            </div>
          </div>
        ))}
      </div>

      {/* Portfolio totals */}
      {portfolio && (
        <div className="mt-4 pt-3 border-t border-gray-800">
          <div className="flex justify-between text-xs">
            <span className="text-gray-400">Total Risk</span>
            <span className="font-medium">{(portfolio.total_risk_pct || 0).toFixed(1)}%</span>
          </div>
          <div className="flex justify-between text-xs mt-1">
            <span className="text-gray-400">Exposure</span>
            <span className="font-medium">{(portfolio.total_exposure_pct || 0).toFixed(1)}%</span>
          </div>
          <div className="flex justify-between text-xs mt-1">
            <span className="text-gray-400">Trading</span>
            <span className={`font-medium ${
              portfolio.trading_allowed ? 'text-profit' : 'text-loss'
            }`}>
              {portfolio.trading_allowed ? 'Active' : 'Halted'}
            </span>
          </div>
        </div>
      )}
    </div>
  )
}

const samplePairs = {
  XAUUSD: { open_trades: 1, total_trades: 12, pnl: 245.50, win_rate: 66.7 },
  XAGUSD: { open_trades: 0, total_trades: 5, pnl: -32.00, win_rate: 40.0 },
  EURUSD: { open_trades: 0, total_trades: 3, pnl: 18.75, win_rate: 66.7 },
}
