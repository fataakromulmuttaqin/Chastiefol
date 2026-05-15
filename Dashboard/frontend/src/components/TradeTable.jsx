import React from 'react'

export default function TradeTable({ trades }) {
  const displayTrades = trades.length > 0 ? trades : sampleTrades

  return (
    <div className="card">
      <h2 className="text-sm font-semibold text-gray-300 mb-4">Recent Trades</h2>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-gray-500 border-b border-gray-800">
              <th className="text-left py-2 px-2">Time</th>
              <th className="text-left py-2 px-2">Symbol</th>
              <th className="text-left py-2 px-2">Direction</th>
              <th className="text-right py-2 px-2">Entry</th>
              <th className="text-right py-2 px-2">Exit</th>
              <th className="text-right py-2 px-2">Lots</th>
              <th className="text-right py-2 px-2">P&L</th>
              <th className="text-center py-2 px-2">Result</th>
            </tr>
          </thead>
          <tbody>
            {displayTrades.map((trade, i) => (
              <tr key={i} className="border-b border-gray-800/50 hover:bg-gray-800/30">
                <td className="py-2 px-2 text-gray-400">
                  {trade.timestamp ? new Date(trade.timestamp).toLocaleTimeString() : '—'}
                </td>
                <td className="py-2 px-2 font-medium">{trade.symbol || 'XAUUSD'}</td>
                <td className="py-2 px-2">
                  <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${
                    trade.direction === 'BUY' ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'
                  }`}>
                    {trade.direction}
                  </span>
                </td>
                <td className="py-2 px-2 text-right">${trade.entry?.toFixed(2) || '—'}</td>
                <td className="py-2 px-2 text-right">${trade.exit?.toFixed(2) || '—'}</td>
                <td className="py-2 px-2 text-right">{trade.lot_size || '—'}</td>
                <td className={`py-2 px-2 text-right font-medium ${
                  (trade.pnl_usd || 0) >= 0 ? 'text-profit' : 'text-loss'
                }`}>
                  {trade.pnl_usd !== undefined ? `${trade.pnl_usd >= 0 ? '+' : ''}$${trade.pnl_usd.toFixed(2)}` : '—'}
                </td>
                <td className="py-2 px-2 text-center">
                  <span className={`text-[10px] font-bold ${
                    trade.outcome === 'WIN' ? 'text-profit' : 'text-loss'
                  }`}>
                    {trade.outcome || '—'}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {displayTrades.length === 0 && (
        <p className="text-center text-gray-600 py-8">No trades yet</p>
      )}
    </div>
  )
}

const sampleTrades = [
  { timestamp: new Date().toISOString(), symbol: 'XAUUSD', direction: 'BUY', entry: 2365.50, exit: 2380.00, lot_size: 0.05, pnl_usd: 72.50, outcome: 'WIN' },
  { timestamp: new Date().toISOString(), symbol: 'XAUUSD', direction: 'SELL', entry: 2382.00, exit: 2375.50, lot_size: 0.03, pnl_usd: 19.50, outcome: 'WIN' },
  { timestamp: new Date().toISOString(), symbol: 'XAUUSD', direction: 'BUY', entry: 2370.00, exit: 2362.00, lot_size: 0.05, pnl_usd: -40.00, outcome: 'LOSS' },
]
