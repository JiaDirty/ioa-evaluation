export function Spinner({ text = '加载中...' }: { text?: string }) {
  return (
    <div className="spinner-container">
      <div className="spinner" />
      <div className="spinner-text">{text}</div>
    </div>
  )
}
