import React from 'react'

export default function Header({ status }) {
  const isOnline = status?.running
  return (
    <header className="max-w-7xl mx-auto flex items-center justify-between">
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 bg-brand-500 rounded-lg flex items-center justify-center font-bold text-lg">
          C
        </div>
        <div>
          <h1 className="text-xl font-bold">Chastiefol</h1>
          <p className="text-xs text-gray-400">XAUUSD Trading Agent</p>
        </div>
      </div>
      <div className="flex items-center gap-4">
        <span className="text-xs text-gray-400">
          Mode: <span className="text-white font-medium">{status?.mode || '—'}</span>
        </span>
        <div className={`flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-medium ${
          isOnline ? 'bg-green-500/10 text-green-400' : 'bg-red-500/10 text-red-400'
        }`}>
          <div className={`w-2 h-2 rounded-full ${isOnline ? 'bg-green-400 animate-pulse' : 'bg-red-400'}`} />
          {isOnline ? 'Online' : 'Offline'}
        </div>
      </div>
    </header>
  )
}
