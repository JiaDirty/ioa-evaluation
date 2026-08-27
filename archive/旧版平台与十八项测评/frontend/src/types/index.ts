export interface ReportSummary {
  id: string
  relative_path?: string
  timestamp: string
  total_tests: number
  passed: number
  failed: number
  scenario_id?: string
  attack_type?: string
  judge_status?: JudgeStatus | ''
  maximum_stage?: string
  status_counts?: Partial<Record<JudgeStatus, number>>
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
    execution_mode?: string
    scientific_use?: string
    judge_status?: JudgeStatus
    attack_triggered?: boolean
    attack_succeeded?: boolean
    maximum_stage?: string
    vulnerable_components?: string[]
    status_counts?: Partial<Record<JudgeStatus, number>>
    judge_status_counts?: Partial<Record<JudgeStatus, number>>
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
  scenario?: ScenarioReportMeta
  scenario_runs?: ScenarioRunSummary[]
  attack_evaluation_bundle?: AttackEvaluationBundle
  judge_verdict?: JudgeVerdict | null
  scenario_evaluation?: ScenarioEvaluation
}

export type JudgeStatus =
  | 'NOT_TRIGGERED'
  | 'ATTEMPTED_BLOCKED'
  | 'PARTIAL_SUCCESS'
  | 'SUCCESS'
  | 'SUCCESS_WITH_IMPACT'
  | 'INDETERMINATE'

export interface ScenarioReportMeta {
  scenario_id: string
  scenario_name: string
  risk_dimension: string
  risk_sub_dimension?: string
  attack_type: string
  difficulty?: string
  source?: string
}

export interface ScenarioEvaluation {
  scenario_valid: boolean
  task_completed: boolean
  attack_triggered: boolean
  judge_status: JudgeStatus
  maximum_stage: string
  vulnerable_components: string[]
  risk_status: JudgeStatus | string
  evaluation_valid: boolean
  passed: boolean
  not_exercised: boolean
  attack_succeeded: boolean
  system_blocked: boolean
  system_recovered: boolean
  consequence_realized: boolean
  reason: string
  invalid_reasons: string[]
  baseline_status?: string
  attack_status?: string
  evidence_ids: string[]
  deterministic_metrics?: Record<string, number | string | string[]>
}

export interface EvidenceCitation {
  event_id: string
  role: string
  supports: string
}

export interface JudgeVerdict {
  attack_type: string
  trigger_assessment: {
    triggered: boolean
    trigger_event_ids: string[]
  }
  injection_assessment: {
    applied: boolean
    attack_event_ids: string[]
  }
  outcome: {
    status: JudgeStatus
    maximum_stage: string
    attack_succeeded: boolean
    consequence_realized: boolean
  }
  system_response: {
    detected: boolean
    blocked: boolean
    contained: boolean
    recovered: boolean
  }
  vulnerability: {
    layers: string[]
    components: string[]
    failure_mechanisms: string[]
  }
  evidence: EvidenceCitation[]
  missing_evidence: string[]
  confidence: number
  reasoning_summary: string
}

export interface AttackEvaluationBundle {
  experiment: Record<string, unknown>
  attack_specification: {
    attack_type?: string
    objective?: string
    trigger_conditions?: unknown[]
    success_stages?: string[]
    required_evidence?: string[]
  }
  attack_injection: {
    prepared?: boolean
    triggered?: boolean
    injection_applied?: boolean
    logs?: AttackLog[]
  }
  task_execution: Record<string, unknown>
  events: Record<string, Record<string, unknown>[]>
  state_snapshots: Record<string, Record<string, unknown>>
  evidence_index: Record<string, Record<string, unknown>>
}

export interface AttackLog {
  evidence_id: string
  attack_type: string
  stage: string
  triggered: boolean
  injection_applied: boolean
  target_event_id?: string | null
  target_event_type?: string | null
  target_component?: string
  modified_object?: string
  before_state?: Record<string, unknown>
  after_state?: Record<string, unknown>
  details?: Record<string, unknown>
  created_at?: string
}

export interface ScenarioRunSummary {
  scenario_id: string
  scenario_name?: string
  attack_type: string
  risk_dimension?: string
  judge_status: JudgeStatus | ''
  maximum_stage: string
  attack_triggered: boolean
  injection_applied: boolean
  vulnerable_components: string[]
  evidence_ids: string[]
  source_report?: string
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

export interface TaskCreateRequest {
  prompt: string
  description?: string
  user_goal?: string
  constraints?: Record<string, unknown>
  execution_mode?: 'agentic' | 'agentic_live' | 'scripted' | 'offline_deterministic'
  origin_sub_ioa?: string | null
  target_sub_ioas?: string[]
  required_capabilities?: string[]
  payload?: Record<string, unknown>
  async_mode?: boolean
}

export interface TaskResponse {
  task_id: string
  trace_id: string
  status: string
  output: unknown
  artifacts: unknown[]
  participating_agents: string[]
  error?: string | null
}

export interface TaskEvent {
  event_id: string
  task_id: string
  trace_id: string
  sequence: number
  span_id: string
  parent_span_id?: string | null
  experiment_id: string
  scenario_id: string
  run_group: string
  graph_id: string
  node_id: string
  stage: string
  event_type: string
  operation: string
  phase: string
  actor_type: string
  actor_id: string
  message: string
  status: string
  payload: Record<string, unknown>
  input: Record<string, unknown>
  output: Record<string, unknown>
  duration_ms?: number | null
  error?: string | null
  created_at: string
}

export interface TaskDetail {
  task_id: string
  request: TaskCreateRequest
  task: Record<string, unknown>
  response: TaskResponse
  feedback: Record<string, unknown>[]
  events: TaskEvent[]
  tool_calls?: ToolCallRecord[]
  artifacts?: unknown[]
}

export interface AgentCard {
  agent_id: string
  display_name: string
  provider: string
  sub_ioa_id: string
  declared_capabilities: string[]
  actual_capabilities?: string[] | null
  supported_protocols: string[]
  endpoint: string
  reputation_score: number
  permission_scope: string[]
  trust_level: string
  status: 'active' | 'suspended' | 'revoked'
  registration_time: string
  updated_at: string
  metadata?: Record<string, unknown>
}

export interface ToolDescriptor {
  tool_id: string
  name: string
  description: string
  input_schema: Record<string, unknown>
  output_schema: Record<string, unknown>
  required_scopes: string[]
  risk_level: 'low' | 'medium' | 'high' | 'critical'
  provider: 'local' | 'mcp' | 'http'
  endpoint: string
  timeout_seconds: number
  metadata: Record<string, unknown>
}

export interface ToolResult {
  call_id: string
  tool_id: string
  status: 'completed' | 'failed' | 'denied'
  output: unknown
  error?: string | null
  metadata: Record<string, unknown>
  created_at: string
}

export interface ToolCallRecord {
  call_id: string
  task_id: string
  trace_id: string
  caller_agent_id: string
  tool_id: string
  status: string
  arguments: Record<string, unknown>
  result: Record<string, unknown>
  error?: string | null
  created_at: string
}

export interface ExecutionNode {
  node_id: string
  node_type: 'verify' | 'policy_check' | 'agent_task' | 'tool' | 'delegation' | 'human' | 'synthesis' | 'agent' | 'aggregate'
  label: string
  status: 'pending' | 'running' | 'completed' | 'failed' | 'skipped' | 'cancelled'
  target_id?: string | null
  depends_on: string[]
  input: Record<string, unknown>
  output: Record<string, unknown>
  error?: string | null
  subtask_description?: string
  required_capabilities?: unknown[]
  assigned_agent_id?: string | null
  assigned_sub_ioa_id?: string | null
  metadata: Record<string, unknown>
}

export interface ExecutionEdge {
  source: string
  target: string
  edge_type: string
  metadata: Record<string, unknown>
}

export interface ExecutionGraph {
  graph_id: string
  task_id: string
  trace_id: string
  failure_strategy: 'fail_fast' | 'continue'
  nodes: ExecutionNode[]
  edges: ExecutionEdge[]
  metadata: Record<string, unknown>
}

export interface SystemGraphNode {
  id: string
  type: string
  label: string
  parent_id: string
  status: string
  metadata: Record<string, unknown>
}

export interface SystemGraphEdge {
  id: string
  source: string
  target: string
  relation: string
}

export interface SystemGraph {
  nodes: SystemGraphNode[]
  edges: SystemGraphEdge[]
  execution_mode: string
}

export interface ObservabilitySpan {
  span_id: string
  parent_span_id?: string | null
  sequence: number
  task_id: string
  trace_id: string
  experiment_id: string
  scenario_id: string
  run_group: string
  graph_id: string
  node_id: string
  span_type: string
  component_type: string
  component_id: string
  operation: string
  status: string
  attempt: number
  started_at?: string | null
  ended_at?: string | null
  duration_ms?: number | null
  input: Record<string, unknown>
  output: Record<string, unknown>
  input_refs: string[]
  output_refs: string[]
  upstream_ids: string[]
  downstream_ids: string[]
  metadata: Record<string, unknown>
  error?: string | null
}

export interface InteractionEdge {
  edge_id: string
  source_id: string
  target_id: string
  relation: string
  span_id: string
  status: string
  sequence: number
  protocol: string
  message: string
  metadata: Record<string, unknown>
}

export interface TaskObservability {
  task: Record<string, unknown> & { task_id: string; trace_id: string; status: string }
  events: TaskEvent[]
  spans: ObservabilitySpan[]
  execution_graph: ExecutionGraph
  interaction_edges: InteractionEdge[]
  tool_calls: ToolCallRecord[]
  artifacts: Record<string, unknown>[]
}

export interface SpanDetail {
  span: ObservabilitySpan
  payloads: Array<{
    payload_id: string
    direction: 'input' | 'output'
    content: unknown
    content_size: number
    truncated: boolean
    created_at: string
  }>
}

export interface McpServerConfig {
  server_id: string
  name: string
  enabled: boolean
  transport: 'http' | 'stdio'
  endpoint?: string | null
  command?: string | null
  args: string[]
  auth: Record<string, unknown>
  allowed_tools: string[]
  timeout_seconds: number
  risk_level: 'low' | 'medium' | 'high' | 'critical'
  sandbox: Record<string, unknown>
  metadata: Record<string, unknown>
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
  judge_status?: JudgeStatus | ''
  maximum_stage?: string
  attack_type?: string
  attack_triggered?: boolean
  injection_applied?: boolean
  vulnerable_components?: string[]
  evidence_ids?: string[]
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
