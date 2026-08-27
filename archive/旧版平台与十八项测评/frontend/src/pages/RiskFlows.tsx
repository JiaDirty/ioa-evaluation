import { useMemo, useState } from 'react'
import { Card } from '../components/Card'
import { Spinner } from '../components/Spinner'
import { ErrorBanner } from '../components/ErrorBanner'
import { EvaluationTopology } from '../components/EvaluationTopology'
import { useApi } from '../hooks/useApi'
import { getRiskTestFlowsDoc } from '../api/client'

interface RiskFlowSection {
  number: number
  name: string
  category: string
  lines: string[]
  codePath: string
  goal: string
  attacks: string[]
}

const CATEGORY_NAMES: Record<string, string> = {
  '一、信任与授权失灵': '信任与授权失灵',
  '二、协议互操作失配': '协议互操作失配',
  '三、互联扩散与可推断性': '互联扩散与可推断性',
  '四、公共知识失真': '公共知识失真',
  '五、生态权力失衡': '生态权力失衡',
  '六、人机能动性侵蚀': '人机能动性侵蚀',
}

const FLOW_STEPS = ['环境准备', '攻击构造', '任务执行', '证据采集', '通过判定']

function stripInlineCode(text: string): string {
  return text.replace(/`([^`]+)`/g, '$1')
}

function parseRiskFlows(markdown: string): RiskFlowSection[] {
  const lines = markdown.split(/\r?\n/)
  const sections: RiskFlowSection[] = []
  let currentCategory = ''
  let current: RiskFlowSection | null = null

  const finish = () => {
    if (!current) return
    const body = current.lines.map(stripInlineCode)
    current.codePath = stripInlineCode(
      body.find(line => line.startsWith('代码位置：'))?.replace('代码位置：', '').trim() || ''
    )
    current.goal = stripInlineCode(
      body.find(line => line.startsWith('测试目标：'))?.replace('测试目标：', '').trim() || ''
    )
    current.attacks = body
      .filter(line => /^(攻击|测试)[一二三四五六七八九十]+：/.test(line.trim()))
      .map(line => line.trim())
    sections.push(current)
  }

  for (const line of lines) {
    const categoryMatch = line.match(/^##\s+(.+)/)
    if (categoryMatch) {
      currentCategory = CATEGORY_NAMES[categoryMatch[1]] || categoryMatch[1]
      continue
    }

    const testMatch = line.match(/^###\s+(\d+)\.\s+(.+)/)
    if (testMatch) {
      finish()
      current = {
        number: Number(testMatch[1]),
        name: testMatch[2].trim(),
        category: currentCategory,
        lines: [],
        codePath: '',
        goal: '',
        attacks: [],
      }
      continue
    }

    if (current) current.lines.push(line)
  }

  finish()
  return sections
}

function renderInline(text: string) {
  const parts = text.split(/(`[^`]+`)/g)
  return parts.map((part, index) => {
    if (part.startsWith('`') && part.endsWith('`')) {
      return <code key={index}>{part.slice(1, -1)}</code>
    }
    return <span key={index}>{part}</span>
  })
}

function MarkdownBlock({ lines }: { lines: string[] }) {
  return (
    <div className="flow-markdown">
      {lines.map((line, index) => {
        const trimmed = line.trim()
        if (!trimmed) return <div key={index} className="flow-space" />
        if (trimmed.startsWith('## ')) return null
        if (trimmed.startsWith('- ')) {
          return <div key={index} className="flow-list-item">{renderInline(trimmed.slice(2))}</div>
        }
        const ordered = trimmed.match(/^(\d+)\.\s+(.+)/)
        if (ordered) {
          return (
            <div key={index} className="flow-order-item">
              <span>{ordered[1]}</span>
              <div>{renderInline(ordered[2])}</div>
            </div>
          )
        }
        if (/^(前置环境|执行链路|执行和判定|观测证据|判定逻辑|最终通过标准|真实性说明|恢复机制)：?$/.test(trimmed)) {
          return <div key={index} className="flow-section-title">{trimmed.replace(/：$/, '')}</div>
        }
        if (/^(攻击|测试)[一二三四五六七八九十]+：/.test(trimmed)) {
          return <div key={index} className="flow-attack-title">{renderInline(trimmed)}</div>
        }
        return <p key={index}>{renderInline(trimmed)}</p>
      })}
    </div>
  )
}

export function RiskFlows() {
  const { data, loading, error, reload } = useApi((signal) => getRiskTestFlowsDoc(signal), [])
  const sections = useMemo(() => parseRiskFlows(data?.markdown || ''), [data])
  const [selectedNumber, setSelectedNumber] = useState(1)
  const selected = sections.find(section => section.number === selectedNumber) || sections[0]

  const categoryCounts = useMemo(() => {
    return sections.reduce<Record<string, number>>((acc, section) => {
      acc[section.category] = (acc[section.category] || 0) + 1
      return acc
    }, {})
  }, [sections])

  if (error) return <ErrorBanner message={`加载 18 项测试流程失败: ${error}`} onRetry={reload} />
  if (loading || !data || !selected) return <Spinner />

  return (
    <div className="risk-flow-page">
      <div className="flow-hero">
        <div>
          <h2>18 个风险测试具体流程</h2>
          <p>从文档自动读取并拆解为可浏览流程，覆盖每项测试的环境、攻击、执行、证据和判定。</p>
        </div>
        <div className="flow-hero-stats">
          <div><strong>{sections.length}</strong><span>测试项</span></div>
          <div><strong>{Object.keys(categoryCounts).length}</strong><span>风险类</span></div>
          <div><strong>{data.line_count}</strong><span>文档行</span></div>
        </div>
      </div>

      <div className="flow-category-row">
        {Object.entries(categoryCounts).map(([category, count]) => (
          <span key={category}>{category}<strong>{count}</strong></span>
        ))}
      </div>

      <div className="flow-layout">
        <aside className="flow-sidebar">
          {sections.map(section => (
            <button
              key={section.number}
              className={section.number === selected.number ? 'active' : ''}
              onClick={() => setSelectedNumber(section.number)}
            >
              <span>{section.number}</span>
              <div>
                <strong>{section.name}</strong>
                <small>{section.category}</small>
              </div>
            </button>
          ))}
        </aside>

        <main className="flow-detail">
          <Card>
            <div className="flow-detail-header">
              <div>
                <div className="flow-kicker">{selected.category}</div>
                <h3>{selected.number}. {selected.name}</h3>
                <p>{selected.goal}</p>
              </div>
              {selected.codePath && <code>{selected.codePath}</code>}
            </div>

            <div className="flow-stepper">
              {FLOW_STEPS.map((step, index) => (
                <div key={step} className="flow-step">
                  <span>{index + 1}</span>
                  <strong>{step}</strong>
                </div>
              ))}
            </div>
          </Card>

          <EvaluationTopology
            testNumber={selected.number}
            category={selected.category}
            name={selected.name}
          />

          <div className="grid-2 flow-summary-grid">
            <Card title="攻击与检查点">
              <div className="flow-attack-list">
                {selected.attacks.length ? selected.attacks.map((attack, index) => (
                  <div key={index} className="flow-attack-card">
                    <span>{index + 1}</span>
                    <p>{stripInlineCode(attack)}</p>
                  </div>
                )) : <div className="empty-state">本文档未显式列出攻击编号</div>}
              </div>
            </Card>
            <Card title="透明化阅读线索">
              <div className="flow-clue-list">
                <div><strong>先看前置环境</strong><span>确认测试创建了哪些子生态、注册表、网关和智能体。</span></div>
                <div><strong>再看攻击构造</strong><span>确认攻击者伪造了什么、修改了什么、诱导了什么。</span></div>
                <div><strong>然后看执行链路</strong><span>确认任务是否经过任务市场、网关、注册表、Agent 和裁判。</span></div>
                <div><strong>最后看判定逻辑</strong><span>确认通过标准、证据字段和真实性边界。</span></div>
              </div>
            </Card>
          </div>

          <Card title="完整实现细节">
            <MarkdownBlock lines={selected.lines} />
          </Card>
        </main>
      </div>
    </div>
  )
}
