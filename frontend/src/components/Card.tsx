import type { ReactNode, CSSProperties } from 'react'

interface CardProps {
  title?: string
  children: ReactNode
  className?: string
  style?: CSSProperties
}

export function Card({ title, children, className = '', style }: CardProps) {
  return (
    <div className={`card ${className}`} style={style}>
      {title && <div className="card-title">{title}</div>}
      {children}
    </div>
  )
}
