import React from 'react'

export default function SignalPanel({ signals }) {
  const displaySignals = signals.length > 0 ? signals : sampleSignals

  return (
    <div className="card h-full">
      <h2 className="text-sm font-semibold text-gray-300 mb-4">Live Signals</h2>
      <div className="space-y-3 max-h-64 overflow-y-auto">
        {displaySignals.map((signal, i) => (
          <div key={i} className={`p-3 rounded-lg border ${
            signal.action === 'BUY'
              ? 'border-green-500/30 bg-green-500/5'
              : signal.action === 'SELL'
              ? 'border-red-500/30 bg-red-500/5'
              : 'border-gray-700 bg-gray-800/30'
          }`}>
            <div className="flex items-center justify-between">
              <span className={`text-xs font-bold px-2 py-0.5 rounded ${
                signal.action === 'BUY' ? 'bg-green-500/20 text-green-400'
                : signal.action === 'SELL' ? 'bg-red-500/20 text-red-400'
                : 'bg-gray-700 text-gray-300'
              }`}>
                {signal.action}
              </span>
              <span className="text-[10px] text-gray-500">
                {signal.timestamp ? new Date(signal.timestamp).toLocaleTimeString() : 'now'}
              </span>
            </div>
            <div className="mt-2 grid grid-cols-2 gap-1 text-[10px] text-gray-400">
              <span>Symbol: <span className="text-white">{signal.symbol || 'XAUUSD'}</span></span>
              <span>Conf: <span className="text-white">{signal.confluence || '—'}/7</span></span>
              <span>SL: <span className="text-white">{signal.sl_pips || '—'} pips</span></span>
              <span>TP: <span className="text-white">{signal.tp_pips || '—'} pips</span></span>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

const sampleSignals = [
  { action: 'BUY', symbol: 'XAUUSD', confluence: 5, sl_pips: 150, tp_pips: 300, timestamp: new Date().toISOString() },
  { action: 'SELL', symbol: 'XAUUSD', confluence: 4, sl_pips: 120, tp_pips: 240, timestamp: new Date().toISOString() },
]
