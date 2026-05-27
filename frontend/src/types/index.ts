export interface ReportSummary {
  id: string
  timestamp: string
  total_tests: number
  passed: number
  failed: number
}

export interface TestResult {
  test_id: string
  test_name: string
  category: string
  passed: boolean
  risk_level: 'HIGH' | 'MEDIUM' | 'LOW'
  confidence: number
  explanation: string
  metrics: Record<string, number>
  details: Record<string, unknown>
  execution_time: number
}

export interface CategoryBreakdown {
  total: number
  passed: number
  failed: number
  tests: Pick<TestResult, 'test_id' | 'passed' | 'risk_level' | 'metrics'>[]
}

export interface ExperimentReport {
  timestamp: string
  summary: {
    total_tests: number
    passed: number
    failed: number
    utility: number
    audit_metrics: {
      chain_completeness: number
      attribution_accuracy: number
      source_coverage: number
      total_entries: number
      total_traces: number
    }
  }
  category_breakdown: Record<string, CategoryBreakdown>
  test_results: TestResult[]
  task_results: unknown[]
  feedback_loop?: FeedbackSummary
  feedback_actions?: FeedbackAction[]
}

export interface FeedbackSummary {
  total_tests: number
  total_passed: number
  total_failed: number
  dimensions: Record<string, {
    name: string
    risk_level: string
    pass_rate: string
    high_risk_tests: string[]
    recommendations: string[]
  }>
  feedback_actions: number
  critical_actions: number
}

export interface FeedbackAction {
  action_id: string
  source_test_id: string
  dimension: string
  action_type: string
  description: string
  priority: 'critical' | 'high' | 'medium' | 'low'
}

export interface SubIoA {
  id: string
  name: string
  agent_name: string
  capabilities: string[]
}

export interface TopologyData {
  style: string
  nodes: string[]
  edges: { source: string; target: string }[]
}

export interface RiskTestFlowDoc {
  title: string
  path: string
  markdown: string
  line_count: number
  updated_at: number
}

export interface WSProgressMessage {
  type: 'progress'
  current: number
  total: number
  test_id: string
  status: string
}

export interface WSResultMessage {
  type: 'result'
  test_id: string
  passed: boolean
  risk_level: string
}

export interface WSCompleteMessage {
  type: 'complete'
  report: ExperimentReport
}

export interface WSErrorMessage {
  type: 'error'
  message: string
}

export type WSMessage = WSProgressMessage | WSResultMessage | WSCompleteMessage | WSErrorMessage
