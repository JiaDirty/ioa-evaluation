import { useState, useEffect } from 'react'
import { Dashboard } from './pages/Dashboard'
import { ExperimentControl } from './pages/ExperimentControl'
import { FeedbackLoopPage } from './pages/FeedbackLoopPage'
import { Topology } from './pages/Topology'
import { RiskFlows } from './pages/RiskFlows'
import { TaskWorkspace } from './pages/TaskWorkspace'
import { AgentRegistry } from './pages/AgentRegistry'
import { ToolRegistry } from './pages/ToolRegistry'
import { TaskDetail } from './pages/TaskDetail'
import { McpRegistry } from './pages/McpRegistry'
import { RuntimeConsole } from './pages/RuntimeConsole'

const PRIMARY_TABS = [
  { id: 'runtime', label: '运行过程' },
  { id: 'dashboard', label: '测评结果' },
  { id: 'flows', label: '18项测评' },
  { id: 'experiment', label: '开始测评' },
]

const MORE_TABS = [
  { id: 'workspace', label: 'IoA 任务' },
  { id: 'task-detail', label: '任务详情' },
  { id: 'agents', label: '智能体管理' },
  { id: 'tools', label: '工具管理' },
  { id: 'mcp', label: '外部工具服务' },
  { id: 'feedback', label: '改进记录' },
  { id: 'topology', label: '网络结构' },
]

const TABS = [...PRIMARY_TABS, ...MORE_TABS]

function getTabFromHash(): string {
  const hash = window.location.hash.replace('#', '').split(':')[0]
  return TABS.some(t => t.id === hash) ? hash : 'runtime'
}

function getTaskIdFromHash(): string {
  const hash = window.location.hash.replace('#', '')
  return hash.startsWith('task-detail:') ? decodeURIComponent(hash.split(':').slice(1).join(':')) : ''
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
          <div className="subtitle">查看测评过程、模型行为与最终结果</div>
        </div>
      </div>

      <div className="tab-nav">
        {PRIMARY_TABS.map(tab => (
          <button
            key={tab.id}
            className={activeTab === tab.id ? 'active' : ''}
            onClick={() => switchTab(tab.id)}
          >
            {tab.label}
          </button>
        ))}
        <details className="nav-more">
          <summary>{MORE_TABS.some(tab => tab.id === activeTab) ? MORE_TABS.find(tab => tab.id === activeTab)?.label : '更多功能'}</summary>
          <div>
            {MORE_TABS.map(tab => (
              <button key={tab.id} className={activeTab === tab.id ? 'active' : ''} onClick={() => switchTab(tab.id)}>
                {tab.label}
              </button>
            ))}
          </div>
        </details>
      </div>

      <div className="tab-content">
        {activeTab === 'runtime' && <RuntimeConsole />}
        {activeTab === 'dashboard' && <Dashboard />}
        {activeTab === 'workspace' && <TaskWorkspace />}
        {activeTab === 'task-detail' && <TaskDetail initialTaskId={getTaskIdFromHash()} />}
        {activeTab === 'agents' && <AgentRegistry />}
        {activeTab === 'tools' && <ToolRegistry />}
        {activeTab === 'mcp' && <McpRegistry />}
        {activeTab === 'flows' && <RiskFlows />}
        {activeTab === 'experiment' && <ExperimentControl />}
        {activeTab === 'feedback' && <FeedbackLoopPage />}
        {activeTab === 'topology' && <Topology />}
      </div>
    </div>
  )
}

export default App
