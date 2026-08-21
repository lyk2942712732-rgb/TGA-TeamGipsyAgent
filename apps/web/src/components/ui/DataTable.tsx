import type { Key, ReactNode } from "react";
import { EmptyState } from "./EmptyState";

export type DataTableColumn<Row> = {
  id: string;
  header: ReactNode;
  render: (row: Row) => ReactNode;
  className?: string;
};

export function DataTable<Row>({
  columns,
  rows,
  rowKey,
  label,
  emptyLabel = "暂无数据",
  onRowClick,
}: {
  columns: DataTableColumn<Row>[];
  rows: Row[];
  rowKey: (row: Row) => Key;
  label: string;
  emptyLabel?: string;
  onRowClick?: (row: Row) => void;
}) {
  if (!rows.length) return <EmptyState label={emptyLabel} />;
  return <div className="data-table-scroll"><table className="data-table" aria-label={label}>
    <thead><tr>{columns.map((column) => <th key={column.id} className={column.className}>{column.header}</th>)}</tr></thead>
    <tbody>{rows.map((row) => <tr key={rowKey(row)} className={onRowClick ? "is-actionable" : undefined} onClick={onRowClick ? () => onRowClick(row) : undefined}>
      {columns.map((column) => <td key={column.id} className={column.className}>{column.render(row)}</td>)}
    </tr>)}</tbody>
  </table></div>;
}
