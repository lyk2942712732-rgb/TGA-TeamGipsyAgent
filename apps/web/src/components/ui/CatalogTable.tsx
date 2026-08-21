import { ChevronLeft, ChevronRight } from "lucide-react";
import type { ReactNode } from "react";

export type Column<T> = {
  id: string;
  header: string;
  render: (row: T) => ReactNode;
  width?: string;
  align?: "start" | "center" | "end";
};

/**
 * The single table style used by every reference-design listing page:
 * light header row, hairline separators, selectable rows.
 */
export function CatalogTable<T>({ columns, rows, rowKey, selectedKey, onSelect, emptyLabel = "暂无数据", label, fill = false }: {
  columns: ReadonlyArray<Column<T>>;
  rows: readonly T[];
  rowKey: (row: T) => string;
  selectedKey?: string;
  onSelect?: (row: T) => void;
  emptyLabel?: string;
  label?: string;
  /** Stretch to the height its parent offers and scroll the body internally. */
  fill?: boolean;
}) {
  if (!rows.length) return <div className={`catalog-table-empty${fill ? " ref-fill" : ""}`}>{emptyLabel}</div>;

  return <div className={`catalog-table-wrap${fill ? " ref-fill" : ""}`}>
    <table className="catalog-table" aria-label={label}>
      <thead>
        <tr>{columns.map((column) => <th
          key={column.id}
          style={{ width: column.width, textAlign: column.align ?? "start" }}
        >{column.header}</th>)}</tr>
      </thead>
      <tbody>
        {rows.map((row) => {
          const key = rowKey(row);
          const selected = key === selectedKey;
          return <tr
            key={key}
            className={`${selected ? "is-selected" : ""} ${onSelect ? "is-clickable" : ""}`}
            aria-selected={onSelect ? selected : undefined}
            tabIndex={onSelect ? 0 : undefined}
            onClick={onSelect ? () => onSelect(row) : undefined}
            onKeyDown={onSelect ? (event) => {
              if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onSelect(row); }
            } : undefined}
          >
            {columns.map((column) => <td
              key={column.id}
              style={{ textAlign: column.align ?? "start" }}
            >{column.render(row)}</td>)}
          </tr>;
        })}
      </tbody>
    </table>
  </div>;
}

const PAGE_SIZES = [10, 20, 50];

/**
 * `共 N 条 · ‹ 1 2 › · 10 条/页` — the footer every listing page in the
 * reference designs carries.  Rendered even for a single page so the tables
 * keep a consistent bottom edge.
 */
export function Pagination({ total, pageSize, page, onPage, onPageSize, unit = "条" }: {
  total: number;
  pageSize: number;
  page: number;
  onPage: (page: number) => void;
  onPageSize?: (size: number) => void;
  unit?: string;
}) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  return <div className="catalog-pagination">
    <span>共 {total} {unit}</span>
    <div className="catalog-pagination-pages">
      <button type="button" aria-label="上一页" disabled={page <= 1} onClick={() => onPage(page - 1)}>
        <ChevronLeft size={14} />
      </button>
      {Array.from({ length: pages }, (_, index) => index + 1).map((value) => <button
        key={value}
        type="button"
        aria-current={value === page ? "page" : undefined}
        className={value === page ? "active" : ""}
        onClick={() => onPage(value)}
      >{value}</button>)}
      <button type="button" aria-label="下一页" disabled={page >= pages} onClick={() => onPage(page + 1)}>
        <ChevronRight size={14} />
      </button>
    </div>
    <select
      aria-label="每页条数"
      value={pageSize}
      disabled={!onPageSize}
      onChange={(event) => onPageSize?.(Number(event.target.value))}
    >
      {PAGE_SIZES.map((size) => <option key={size} value={size}>{size} 条/页</option>)}
    </select>
  </div>;
}

/** Slices `rows` for the current page and clamps the page when filters shrink the set. */
export function usePage<T>(rows: readonly T[], pageSize: number, page: number): readonly T[] {
  const pages = Math.max(1, Math.ceil(rows.length / pageSize));
  const safe = Math.min(page, pages);
  return rows.slice((safe - 1) * pageSize, safe * pageSize);
}
