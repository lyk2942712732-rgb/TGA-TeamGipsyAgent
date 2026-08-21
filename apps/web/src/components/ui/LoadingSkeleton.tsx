export function LoadingSkeleton({ label = "正在加载", rows = 4 }: { label?: string; rows?: number }) {
  return <section className="loading-skeleton" aria-busy="true" aria-label={label}>
    <span className="sr-only">{label}</span>
    <div className="skeleton-heading" />
    {Array.from({ length: rows }, (_, index) => <div className="skeleton-row" key={index} />)}
  </section>;
}
