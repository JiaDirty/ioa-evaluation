interface BadgeProps {
  type: 'pass' | 'fail' | 'high' | 'medium' | 'low'
  children: React.ReactNode
}

export function Badge({ type, children }: BadgeProps) {
  return <span className={`badge badge-${type}`}>{children}</span>
}
