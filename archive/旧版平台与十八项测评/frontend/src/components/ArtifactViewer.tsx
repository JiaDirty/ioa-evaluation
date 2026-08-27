interface ArtifactViewerProps {
  artifacts: unknown[]
}

export function ArtifactViewer({ artifacts }: ArtifactViewerProps) {
  if (!artifacts.length) {
    return <div className="empty-state">暂无产物</div>
  }
  return (
    <div className="artifact-list">
      {artifacts.map((artifact, index) => (
        <pre key={index} className="artifact-json">
          {JSON.stringify(artifact, null, 2)}
        </pre>
      ))}
    </div>
  )
}
