interface ErrorBannerProps {
  message: string
  onRetry?: () => void
}

export function ErrorBanner({ message, onRetry }: ErrorBannerProps) {
  return (
    <div className="error-banner">
      <span className="error-banner-icon">!</span>
      <span className="error-banner-text">{message}</span>
      {onRetry && (
        <button className="error-banner-retry" onClick={onRetry}>
          重试
        </button>
      )}
    </div>
  )
}
