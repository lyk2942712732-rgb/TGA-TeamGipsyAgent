import type { ReactNode } from "react";

export type Field = {
  label: string;
  value?: ReactNode;
  /**
   * Reference design shows this field but no API supplies it.  Rendered as a
   * plain dash — the "项目没有实现" markers were pulled from the product on
   * request; the gap is tracked in the handover notes instead.
   */
  missing?: boolean;
};

/** Label/value rows used by every detail panel in the reference designs. */
export function FieldGrid({ fields, columns = 1 }: { fields: Field[]; columns?: 1 | 2 }) {
  return <dl className={`field-grid cols-${columns}`}>
    {fields.map((field) => <div className="field-grid-row" key={field.label}>
      <dt>{field.label}</dt>
      <dd>{field.missing || field.value == null ? <span className="field-empty">—</span> : field.value}</dd>
    </div>)}
  </dl>;
}

export function ChipList({ values, tone = "accent" }: { values: readonly string[]; tone?: "accent" | "neutral" }) {
  if (!values.length) return <span className="field-empty">—</span>;
  return <span className="chip-list">{values.map((value) => <span className={`chip tone-${tone}`} key={value}>{value}</span>)}</span>;
}
