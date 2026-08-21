import type { ReactNode } from "react";
import { Breadcrumbs, type BreadcrumbItem } from "./Breadcrumbs";

export function PageHeader({
  eyebrow,
  title,
  description,
  breadcrumbs = [],
  actions,
  meta,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  breadcrumbs?: BreadcrumbItem[];
  actions?: ReactNode;
  meta?: ReactNode;
}) {
  return <header className="page-header-v2">
    <div className="page-header-copy">
      {breadcrumbs.length ? <Breadcrumbs items={breadcrumbs} /> : null}
      {eyebrow ? <span className="page-header-eyebrow">{eyebrow}</span> : null}
      <div className="page-header-title"><h1>{title}</h1>{meta}</div>
      {description ? <p>{description}</p> : null}
    </div>
    {actions ? <div className="page-header-actions">{actions}</div> : null}
  </header>;
}
