import React from 'react'
import Header from './components/Header'
import StatusCards from './components/StatusCards'
import EquityChart from './components/EquityChart'
import TradeTable from './components/TradeTable'
import SignalPanel from './components/SignalPanel'
import PortfolioView from './components/PortfolioView'
import { useApi } from './hooks/useApi'

export default function App() {
  const { data: status, loading: statusLoading } = useApi('/status', 3000)
  const { data: trades } = useApi('/trades?limit=20', 5000)
  const { data: equity } = useApi('/equity', 10000)
  const { data: signals } = useApi('/signals?limit=10', 5000)
  const { data: portfolio } = useApi('/portfolio', 5000)

  return (
    <div className="min-h-screen bg-gray-950 p-4 md:p-6">
      <Header status={status} />

      <div className="max-w-7xl mx-auto space-y-6 mt-6">
        {/* Status Cards Row */}
        <StatusCards status={status} loading={statusLoading} />

        {/* Main Grid */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Equity Chart — 2 cols */}
          <div className="lg:col-span-2">
            <EquityChart data={equity?.curve || []} />
          </div>

          {/* Signal Panel — 1 col */}
          <div>
            <SignalPanel signals={signals?.signals || []} />
          </div>
        </div>

        {/* Portfolio & Trades */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="lg:col-span-2">
            <TradeTable trades={trades?.trades || []} />
          </div>
          <div>
            <PortfolioView portfolio={portfolio} />
          </div>
        </div>
      </div>
    </div>
  )
}
