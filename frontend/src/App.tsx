import { useState, useEffect } from 'react'
import { Dashboard } from './pages/Dashboard'
import { ExperimentControl } from './pages/ExperimentControl'
import { FeedbackLoopPage } from './pages/FeedbackLoopPage'
import { Topology } from './pages/Topology'
import { RiskFlows } from './pages/RiskFlows'

const TABS = [
  { id: 'dashboard', label: '📊 测试仪表盘' },
  { id: 'flows', label: '🔎 18项流程' },
  { id: 'experiment', label: '🧪 实验控制' },
  { id: 'feedback', label: '🔄 反馈循环' },
  { id: 'topology', label: '🕸️ Agent 拓扑' },
]

function getTabFromHash(): string {
  const hash = window.location.hash.replace('#', '')
  return TABS.some(t => t.id === hash) ? hash : 'dashboard'
}

function App() {
  const [activeTab, setActiveTab] = useState(getTabFromHash)

  useEffect(() => {
    const onHashChange = () => setActiveTab(getTabFromHash())
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [])

  const switchTab = (id: string) => {
    window.location.hash = id
    setActiveTab(id)
  }

  return (
    <div className="app">
      <div className="app-header">
        <div>
          <h1>IOA 安全测评控制台</h1>
          <div className="subtitle">Internet of Agents Security Evaluation Console</div>
        </div>
      </div>

      <div className="tab-nav">
        {TABS.map(tab => (
          <button
            key={tab.id}
            className={activeTab === tab.id ? 'active' : ''}
            onClick={() => switchTab(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      <div className="tab-content">
        {activeTab === 'dashboard' && <Dashboard />}
        {activeTab === 'flows' && <RiskFlows />}
        {activeTab === 'experiment' && <ExperimentControl />}
        {activeTab === 'feedback' && <FeedbackLoopPage />}
        {activeTab === 'topology' && <Topology />}
      </div>
    </div>
  )
}

export default App
