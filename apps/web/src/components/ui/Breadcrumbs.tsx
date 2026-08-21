import { ChevronRight } from "lucide-react";

export type BreadcrumbItem = { label: string; href?: string };

export function Breadcrumbs({ items }: { items: BreadcrumbItem[] }) {
  return <nav className="breadcrumbs" aria-label="面包屑">
    <ol>{items.map((item, index) => <li key={`${item.label}:${index}`}>
      {index ? <ChevronRight size={12} aria-hidden="true" /> : null}
      {item.href && index < items.length - 1 ? <a href={item.href}>{item.label}</a> : <span aria-current={index === items.length - 1 ? "page" : undefined}>{item.label}</span>}
    </li>)}</ol>
  </nav>;
}
