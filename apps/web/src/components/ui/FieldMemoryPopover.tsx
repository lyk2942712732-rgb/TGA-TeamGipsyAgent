import { History, RotateCcw, Trash2 } from "lucide-react";
import { type ReactNode, useEffect, useRef, useState } from "react";
import { formatShortTime, type FieldHistoryEntry } from "../../pages/task-form-memory";

/** Wraps an input/textarea with hover-triggered history quick-fill and one-click undo. */
export function MemoryField({
  className = "",
  label,
  subLabel,
  fieldKey,
  currentValue,
  items,
  multiline = false,
  compact = false,
  undoAvailable = false,
  onUndo,
  onSelect,
  onForget,
  children,
}: {
  className?: string;
  label: string;
  subLabel?: string;
  fieldKey: string;
  currentValue: string;
  items: FieldHistoryEntry[];
  multiline?: boolean;
  compact?: boolean;
  undoAvailable?: boolean;
  onUndo: () => void;
  onSelect: (value: string) => void;
  onForget: (value: string) => void;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const closeTimerRef = useRef<number | null>(null);
  const suggestions = items.filter((item) => item.value !== currentValue);

  function cancelClose() {
    if (closeTimerRef.current !== null) {
      window.clearTimeout(closeTimerRef.current);
      closeTimerRef.current = null;
    }
  }

  function openNow() {
    cancelClose();
    if (suggestions.length > 0) setOpen(true);
  }

  function scheduleClose() {
    cancelClose();
    closeTimerRef.current = window.setTimeout(() => setOpen(false), 180);
  }

  useEffect(() => () => cancelClose(), []);

  const willReplace = Boolean(currentValue.trim());

  return (
    <div
      className={`memory-field-wrap${compact ? " is-compact" : ""} ${className}`.trim()}
      onMouseEnter={openNow}
      onMouseLeave={scheduleClose}
      data-field={fieldKey}
    >
      <label className={className}>
        <span className="memory-field-label-row">
          <span>{label}{subLabel ? <span className="memory-field-sub">{subLabel}</span> : null}</span>
        </span>
        {children}
      </label>
      {undoAvailable ? (
        <button type="button" className="text-button memory-undo-btn" onClick={onUndo}>
          <RotateCcw size={12} />撤销填入
        </button>
      ) : null}

      {open && suggestions.length > 0 ? (
        <div
          className={`memory-popover${multiline ? " is-multiline" : ""}`}
          role="region"
          aria-label={`${label} 历史记忆`}
          onMouseEnter={cancelClose}
          onMouseLeave={scheduleClose}
        >
          <header>
            <span><History size={12} /> 上次填写{willReplace ? "（点击将替换当前内容）" : "（点击快速填入）"}</span>
          </header>
          <ul>
            {suggestions.map((entry, idx) => (
              <li key={`${entry.savedAt}-${idx}`}>
                <button
                  type="button"
                  className="memory-option-btn"
                  onClick={() => {
                    onSelect(entry.value);
                    setOpen(false);
                  }}
                >
                  <span className="memory-option-text">
                    {multiline && entry.value.length > 140 ? `${entry.value.slice(0, 140)}…` : entry.value}
                  </span>
                  <small>
                    {idx === 0 ? "最近一次" : "较早记录"}
                    {multiline ? ` · ${entry.value.length} 字` : ""}
                    {formatShortTime(entry.savedAt) ? ` · ${formatShortTime(entry.savedAt)}` : ""}
                  </small>
                </button>
                <button
                  type="button"
                  className="memory-forget-btn"
                  aria-label={`清除这条${label}记忆`}
                  title="清除此条记忆"
                  onClick={(event) => {
                    event.stopPropagation();
                    onForget(entry.value);
                  }}
                >
                  <Trash2 size={12} />
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
