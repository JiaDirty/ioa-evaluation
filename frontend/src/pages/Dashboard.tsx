import { RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis, Radar, ResponsiveContainer, BarChart, Bar, XAxis, YAxis, Tooltip, Legend } from 'recharts'
import { Card } from '../components/Card'
import { Badge } from '../components/Badge'
import { DataTable } from '../components/DataTable'
import { Spinner } from '../components/Spinner'
import { ErrorBanner } from '../components/ErrorBanner'
import { useApi } from '../hooks/useApi'
import { listReports, getReport } from '../api/client'
import { useState } from 'react'
import type { TestResult } from '../types'

const CATEGORY_NAMES: Record<string, string> = {
  trust_authorization: '信任授权',
  protocol_interop: '协议互操作',
  interconnection: '互联扩散',
  public_knowledge: '公共知识',
  power_imbalance: '权力失衡',
  human_agency: '人机能动性',
}

export function Dashboard() {
  const { data: reports, error: reportsError, reload: reloadReports } = useApi(
    (signal) => listReports(signal),
    []
  )
  const [selectedId, setSelectedId] = useState<string | null>(null)

  const activeId = selectedId || reports?.[0]?.id
  const { data: report, loading, error: reportError, reload: reloadReport } = useApi(
    (signal) => activeId ? getReport(activeId, signal) : Promise.reject('No reports'),
    [activeId, reports]
  )

  if (reportsError) return <ErrorBanner message={`加载报告列表失败: ${reportsError}`} onRetry={reloadReports} />
  if (reportError) return <ErrorBanner message={`加载报告失败: ${reportError}`} onRetry={reloadReport} />
  if (loading || !report) return <Spinner />

  const { summary, category_breakdown, test_results } = report

  const radarData = Object.entries(category_breakdown).map(([cat, data]) => ({
    dimension: CATEGORY_NAMES[cat] || cat,
    score: data.total > 0 ? Math.round((data.passed / data.total) * 100) : 0,
    fullMark: 100,
  }))

  const barData = Object.entries(category_breakdown).map(([cat, data]) => ({
    name: CATEGORY_NAMES[cat] || cat,
    通过: data.passed,
    失败: data.failed,
  }))

  const testColumns = [
    { key: 'test_id', header: '测试ID', render: (r: TestResult) => <code style={{ fontSize: 12 }}>{r.test_id}</code> },
    { key: 'test_name', header: '名称', render: (r: TestResult) => r.test_name },
    { key: 'category', header: '类别', render: (r: TestResult) => CATEGORY_NAMES[r.category] || r.category },
    {
      key: 'status', header: '状态',
      render: (r: TestResult) => <Badge type={r.passed ? 'pass' : 'fail'}>{r.passed ? 'PASS' : 'FAIL'}</Badge>,
    },
    {
      key: 'risk', header: '风险',
      render: (r: TestResult) => <Badge type={r.risk_level.toLowerCase() as 'high' | 'medium' | 'low'}>{r.risk_level}</Badge>,
    },
  ]

  return (
    <div>
      {reports && reports.length > 1 && (
        <div style={{ marginBottom: 16 }}>
          <select
            value={selectedId || reports[0]?.id || ''}
            onChange={e => setSelectedId(e.target.value)}
            style={{ padding: '6px 12px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13 }}
          >
            {reports.map(r => <option key={r.id} value={r.id}>{r.id} ({r.timestamp})</option>)}
          </select>
        </div>
      )}

      <div className="grid-4" style={{ marginBottom: 20 }}>
        <Card>
          <div style={{ textAlign: 'center' }}>
            <div style={{ fontSize: 28, fontWeight: 700, color: 'var(--color-blue)' }}>{summary.total_tests}</div>
            <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>总测试数</div>
          </div>
        </Card>
        <Card>
          <div style={{ textAlign: 'center' }}>
            <div style={{ fontSize: 28, fontWeight: 700, color: 'var(--color-green)' }}>{summary.passed}</div>
            <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>通过</div>
          </div>
        </Card>
        <Card>
          <div style={{ textAlign: 'center' }}>
            <div style={{ fontSize: 28, fontWeight: 700, color: 'var(--color-red)' }}>{summary.failed}</div>
            <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>失败</div>
          </div>
        </Card>
        <Card>
          <div style={{ textAlign: 'center' }}>
            <div style={{ fontSize: 28, fontWeight: 700, color: 'var(--color-blue)' }}>
              {summary.total_tests > 0 ? Math.round((summary.passed / summary.total_tests) * 100) : 0}%
            </div>
            <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>通过率</div>
          </div>
        </Card>
      </div>

      <div className="grid-2" style={{ marginBottom: 20 }}>
        <Card title="六维风险雷达">
          <ResponsiveContainer width="100%" height={300}>
            <RadarChart data={radarData}>
              <PolarGrid />
              <PolarAngleAxis dataKey="dimension" tick={{ fontSize: 12 }} />
              <PolarRadiusAxis angle={30} domain={[0, 100]} tick={{ fontSize: 10 }} />
              <Radar name="通过率" dataKey="score" stroke="#0969da" fill="#0969da" fillOpacity={0.3} />
            </RadarChart>
          </ResponsiveContainer>
        </Card>
        <Card title="风险类别分布">
          <ResponsiveContainer width="100%" height={300}>
            <BarChart data={barData}>
              <XAxis dataKey="name" tick={{ fontSize: 11 }} />
              <YAxis />
              <Tooltip />
              <Legend />
              <Bar dataKey="通过" stackId="a" fill="#1a7f37" />
              <Bar dataKey="失败" stackId="a" fill="#cf222e" />
            </BarChart>
          </ResponsiveContainer>
        </Card>
      </div>

      <Card title="测试明细">
        <DataTable columns={testColumns} data={test_results} getRowKey={r => r.test_id} />
      </Card>
    </div>
  )
}
