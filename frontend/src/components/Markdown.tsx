import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import styles from "./Markdown.module.css";

// react-markdown does NOT render raw HTML by default (no rehype-raw here), so
// digest content can't inject markup — safe. External links open in a new tab
// with noopener.
export function Markdown({ children }: { children: string }) {
  return (
    <div className={styles.markdown}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children: linkChildren }) => (
            <a href={href} target="_blank" rel="noopener noreferrer">
              {linkChildren}
            </a>
          ),
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
