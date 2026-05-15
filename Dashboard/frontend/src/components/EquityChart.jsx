import React from 'react'
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Area, AreaChart } from 'recharts'

export default function EquityChart({ data }) {
  // Generate sample data if empty
  const chartData = data.length > 0 ? data : generateSampleData()

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-sm font-semibold text-gray-300">Equity Curve</h2>
        <span className="text-xs text-gray-500">{chartData.length} data points</span>
      </div>
      <div className="h-64">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={chartData} margin={{ top: 5, right: 10, left: 10, bottom: 5 }}>
            <defs>
              <linearGradient id="equityGradient" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#7C3AED" stopOpacity={0.3} />
                <stop offset="95%" stopColor="#7C3AED" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#333" />
            <XAxis dataKey="time" tick={{ fontSize: 10, fill: '#666' }} />
            <YAxis tick={{ fontSize: 10, fill: '#666' }} domain={['auto', 'auto']} />
            <Tooltip
              contentStyle={{ backgroundColor: '#1a1a2e', border: '1px solid #333', borderRadius: 8 }}
              labelStyle={{ color: '#999' }}
            />
            <Area
              type="monotone"
              dataKey="equity"
              stroke="#7C3AED"
              strokeWidth={2}
              fill="url(#equityGradient)"
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}

function generateSampleData() {
  const data = []
  let equity = 10000
  for (let i = 0; i < 50; i++) {
    equity += (Math.random() - 0.45) * 50
    data.push({ time: `D${i + 1}`, equity: Math.round(equity * 100) / 100 })
  }
  return data
}
