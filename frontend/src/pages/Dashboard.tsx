import { Bar, BarChart, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { Card } from '../components/Card'
import { Badge } from '../components/Badge'
import { DataTable } from '../components/DataTable'
import { Spinner } from '../components/Spinner'
import { ErrorBanner } from '../components/ErrorBanner'
import { useApi } from '../hooks/useApi'
import { listReports, getReport } from '../api/client'
import { useState } from 'react'
import type {
  AttackLog,
  EvidenceCitation,
  ExperimentReport,
  JudgeStatus,
  ScenarioRunSummary,
  TestResult,
} from '../types'

const CATEGORY_NAMES: Record<string, string> = {
  trust_authorization: '信任授权',
  protocol_interop: '协议互操作',
  interconnection: '互联扩散',
  public_knowledge: '公共知识',
  power_imbalance: '权力失衡',
  human_agency: '人机能动性',
}

const JUDGE_STATUSES: JudgeStatus[] = [
  'NOT_TRIGGERED',
  'ATTEMPTED_BLOCKED',
  'PARTIAL_SUCCESS',
  'SUCCESS',
  'SUCCESS_WITH_IMPACT',
  'INDETERMINATE',
]

const STATUS_LABELS: Record<JudgeStatus, string> = {
  NOT_TRIGGERED: '未触发',
  ATTEMPTED_BLOCKED: '已尝试并阻断',
  PARTIAL_SUCCESS: '部分成功',
  SUCCESS: '成功',
  SUCCESS_WITH_IMPACT: '成功且有后果',
  INDETERMINATE: '证据不足',
}

function isJudgeReport(report: ExperimentReport): boolean {
  return Boolean(report.judge_verdict || report.scenario_evaluation || report.scenario_runs?.length)
}

function statusClass(status: string | undefined): string {
  const normalized = status || 'INDETERMINATE'
  if (normalized === 'SUCCESS' || normalized === 'SUCCESS_WITH_IMPACT') return 'danger'
  if (normalized === 'PARTIAL_SUCCESS') return 'warning'
  if (normalized === 'ATTEMPTED_BLOCKED') return 'success'
  if (normalized === 'NOT_TRIGGERED') return 'neutral'
  return 'muted'
}

function formatStatus(status: string | undefined): string {
  if (!status) return '未知'
  return STATUS_LABELS[status as JudgeStatus] || status
}

function getStatusCounts(report: ExperimentReport): Record<JudgeStatus, number> {
  const raw = report.summary?.status_counts || report.summary?.judge_status_counts || {}
  return Object.fromEntries(
    JUDGE_STATUSES.map(status => [status, Number(raw[status] || 0)])
  ) as Record<JudgeStatus, number>
}

function evidenceIds(report: ExperimentReport): string[] {
  return report.scenario_evaluation?.evidence_ids
    || report.judge_verdict?.evidence.map(item => item.event_id)
    || []
}

function JudgeStatusBadge({ status }: { status?: string }) {
  return <span className={`judge-badge judge-badge-${statusClass(status)}`}>{formatStatus(status)}</span>
}

function JudgeReportView({ report }: { report: ExperimentReport }) {
  const status = report.judge_verdict?.outcome.status || report.scenario_evaluation?.judge_status
  const statusCounts = getStatusCounts(report)
  const statusChart = JUDGE_STATUSES.map(s => ({ status: formatStatus(s), count: statusCounts[s] }))
  const attackLogs = report.attack_evaluation_bundle?.attack_injection.logs || []
  const citations = report.judge_verdict?.evidence || []
  const runs = report.scenario_runs || []
  const scenario = report.scenario
  const spec = report.attack_evaluation_bundle?.attack_specification
  const vulnerable = report.judge_verdict?.vulnerability.components
    || report.scenario_evaluation?.vulnerable_components
    || []
  const ids = evidenceIds(report)

  return (
    <div className="judge-dashboard">
      <div className="grid-4 judge-kpis">
        <Card>
          <strong>{report.summary?.total_tests || runs.length || 1}</strong>
          <span>场景数</span>
        </Card>
        <Card>
          <strong>{formatStatus(status || report.summary?.judge_status)}</strong>
          <span>Judge 状态</span>
        </Card>
        <Card>
          <strong>{report.judge_verdict?.outcome.maximum_stage || report.scenario_evaluation?.maximum_stage || '-'}</strong>
          <span>最大阶段</span>
        </Card>
        <Card>
          <strong>{ids.length}</strong>
          <span>Evidence ID</span>
        </Card>
      </div>

      <div className="grid-2" style={{ marginBottom: 20 }}>
        <Card title="Judge 状态分布">
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={statusChart}>
              <XAxis dataKey="status" tick={{ fontSize: 11 }} />
              <YAxis allowDecimals={false} />
              <Tooltip />
              <Legend />
              <Bar dataKey="count" name="场景数" fill="#0969da" />
            </BarChart>
          </ResponsiveContainer>
        </Card>

        <Card title="当前报告">
          <div className="judge-facts">
            <div>
              <span>运行模式</span>
              <strong>{report.summary?.execution_mode || '-'}</strong>
            </div>
            <div>
              <span>科学用途</span>
              <strong>{report.summary?.scientific_use || 'seed/Judge validation'}</strong>
            </div>
            <div>
              <span>攻击触发</span>
              <strong>{report.scenario_evaluation?.attack_triggered || report.judge_verdict?.trigger_assessment.triggered ? '是' : '否'}</strong>
            </div>
            <div>
              <span>注入执行</span>
              <strong>{report.judge_verdict?.injection_assessment.applied ? '是' : '否'}</strong>
            </div>
            <div>
              <span>系统阻断</span>
              <strong>{report.judge_verdict?.system_response.blocked || report.scenario_evaluation?.system_blocked ? '是' : '否'}</strong>
            </div>
            <div>
              <span>后果确认</span>
              <strong>{report.judge_verdict?.outcome.consequence_realized || report.scenario_evaluation?.consequence_realized ? '是' : '否'}</strong>
            </div>
          </div>
        </Card>
      </div>

      {runs.length > 0 && (
        <Card title="Seed Judge 明细" style={{ marginBottom: 20 }}>
          <DataTable
            columns={[
              { key: 'scenario', header: 'Seed', render: (r: ScenarioRunSummary) => <code>{r.scenario_id}</code> },
              { key: 'attack', header: '攻击类型', render: (r: ScenarioRunSummary) => r.attack_type },
              { key: 'status', header: 'Judge', render: (r: ScenarioRunSummary) => <JudgeStatusBadge status={r.judge_status} /> },
              { key: 'stage', header: '最大阶段', render: (r: ScenarioRunSummary) => r.maximum_stage || '-' },
              { key: 'evidence', header: '证据数', render: (r: ScenarioRunSummary) => r.evidence_ids?.length || 0 },
            ]}
            data={runs}
            getRowKey={r => r.scenario_id}
          />
        </Card>
      )}

      {scenario && (
        <Card title="单 Seed 攻击裁判" style={{ marginBottom: 20 }}>
          <div className="judge-scenario-header">
            <div>
              <div className="flow-kicker">{scenario.scenario_id} · {scenario.attack_type}</div>
              <h2>{scenario.scenario_name}</h2>
              <p>{CATEGORY_NAMES[scenario.risk_dimension] || scenario.risk_dimension}</p>
            </div>
            <JudgeStatusBadge status={status} />
          </div>

          <div className="judge-facts compact">
            <div>
              <span>Objective</span>
              <strong>{spec?.objective || '-'}</strong>
            </div>
            <div>
              <span>Success stages</span>
              <strong>{spec?.success_stages?.join(' → ') || '-'}</strong>
            </div>
            <div>
              <span>Required evidence</span>
              <strong>{spec?.required_evidence?.join(', ') || '-'}</strong>
            </div>
            <div>
              <span>Vulnerable components</span>
              <strong>{vulnerable.length ? vulnerable.join(', ') : '-'}</strong>
            </div>
          </div>

          {report.judge_verdict?.reasoning_summary && (
            <div className="judge-reasoning">{report.judge_verdict.reasoning_summary}</div>
          )}
        </Card>
      )}

      {attackLogs.length > 0 && (
        <Card title="Evidence Timeline" style={{ marginBottom: 20 }}>
          <DataTable
            columns={[
              { key: 'id', header: 'Evidence ID', render: (r: AttackLog) => <code>{r.evidence_id}</code> },
              { key: 'stage', header: '阶段', render: (r: AttackLog) => r.stage },
              { key: 'component', header: '组件', render: (r: AttackLog) => r.target_component || '-' },
              { key: 'object', header: '对象', render: (r: AttackLog) => r.modified_object || '-' },
              { key: 'applied', header: '注入', render: (r: AttackLog) => r.injection_applied ? '是' : '否' },
            ]}
            data={attackLogs}
            getRowKey={r => r.evidence_id}
          />
        </Card>
      )}

      {citations.length > 0 && (
        <Card title="Judge Evidence Citations">
          <DataTable
            columns={[
              { key: 'event', header: 'Evidence ID', render: (r: EvidenceCitation) => <code>{r.event_id}</code> },
              { key: 'role', header: '角色', render: (r: EvidenceCitation) => r.role },
              { key: 'supports', header: '支持结论', render: (r: EvidenceCitation) => r.supports },
            ]}
            data={citations}
            getRowKey={r => `${r.event_id}-${r.supports}`}
          />
        </Card>
      )}
    </div>
  )
}

function LegacyReportView({ report }: { report: ExperimentReport }) {
  const { summary, category_breakdown = {}, test_results = [] } = report
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
      key: 'status',
      header: '兼容状态',
      render: (r: TestResult) => <Badge type={r.passed ? 'pass' : 'fail'}>{r.passed ? 'PASS' : 'FAIL'}</Badge>,
    },
    {
      key: 'risk',
      header: '风险',
      render: (r: TestResult) => <Badge type={r.risk_level.toLowerCase() as 'high' | 'medium' | 'low'}>{r.risk_level}</Badge>,
    },
  ]

  return (
    <div>
      <div className="grid-4" style={{ marginBottom: 20 }}>
        <Card><div className="legacy-kpi"><strong>{summary.total_tests || 0}</strong><span>总测试数</span></div></Card>
        <Card><div className="legacy-kpi"><strong>{summary.passed || 0}</strong><span>兼容通过</span></div></Card>
        <Card><div className="legacy-kpi"><strong>{summary.failed || 0}</strong><span>兼容失败</span></div></Card>
        <Card><div className="legacy-kpi"><strong>{summary.execution_mode || 'legacy'}</strong><span>报告类型</span></div></Card>
      </div>

      <Card title="历史风险类别分布" style={{ marginBottom: 20 }}>
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

      <Card title="历史测试明细">
        <DataTable columns={testColumns} data={test_results} getRowKey={r => r.test_id} />
      </Card>
    </div>
  )
}

export function Dashboard() {
  const { data: reports, error: reportsError, reload: reloadReports } = useApi(
    (signal) => listReports(signal),
    []
  )
  const [selectedId, setSelectedId] = useState<string | null>(null)

  const activeId = selectedId || reports?.[0]?.id
  const { data: report, loading, error: reportError, reload: reloadReport } = useApi(
    (signal) => activeId ? getReport(activeId, signal) : Promise.reject(new Error('No reports')),
    [activeId, reports]
  )

  if (reportsError) return <ErrorBanner message={`加载报告列表失败: ${reportsError}`} onRetry={reloadReports} />
  if (reportError) return <ErrorBanner message={`加载报告失败: ${reportError}`} onRetry={reloadReport} />
  if (loading || !report) return <Spinner />

  return (
    <div>
      {reports && reports.length > 0 && (
        <div className="report-picker">
          <select
            value={activeId || ''}
            onChange={e => setSelectedId(e.target.value)}
          >
            {reports.map(r => (
              <option key={r.id} value={r.id}>
                {r.scenario_id || r.id} {r.judge_status ? `· ${formatStatus(r.judge_status)}` : ''} ({r.timestamp || r.relative_path})
              </option>
            ))}
          </select>
          <span>{reports.length} 个报告可查看</span>
        </div>
      )}

      {isJudgeReport(report) ? <JudgeReportView report={report} /> : <LegacyReportView report={report} />}
    </div>
  )
}
