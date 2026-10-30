import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/** Render model-produced Markdown as React nodes, without executing embedded HTML. */
export function Markdown({ content }: { content: string }) {
  return <div className="workbench-markdown"><ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml components={{
    img: ({ alt }) => <span className="cell-muted">[图片：{alt || "未命名"}]</span>,
    a: ({ href, children }) => <a href={href} target="_blank" rel="noreferrer">{children}</a>,
    table: ({ children }) => <div className="catalog-table-wrap"><table className="catalog-table">{children}</table></div>,
  }}>{content}</ReactMarkdown></div>;
}
