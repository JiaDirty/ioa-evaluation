import type { ExecutionGraph as ExecutionGraphModel } from '../types'

interface ExecutionGraphProps {
  graph: ExecutionGraphModel | null
}

export function ExecutionGraph({ graph }: ExecutionGraphProps) {
  if (!graph || !graph.nodes.length) {
    return <div className="empty-state">暂无执行图</div>
  }

  return (
    <div className="execution-graph">
      {graph.nodes.map(node => (
        <div key={node.node_id} className={`execution-node ${node.status}`}>
          <div>
            <strong>{node.label}</strong>
            <span>{node.node_type} · {node.status}</span>
          </div>
          {node.target_id && <small>target: {node.target_id}</small>}
          {node.depends_on.length > 0 && <small>depends: {node.depends_on.join(', ')}</small>}
          {node.error && <p>{node.error}</p>}
        </div>
      ))}
    </div>
  )
}
