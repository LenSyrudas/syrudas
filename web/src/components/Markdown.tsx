import { useEffect, useRef, useState } from 'react'
import type { ComponentPropsWithoutRef } from 'react'
import ReactMarkdown from 'react-markdown'
import type { ExtraProps } from 'react-markdown'
import rehypeHighlight from 'rehype-highlight'
import remarkGfm from 'remark-gfm'
import { copyToClipboard } from '../clipboard'

/** A fenced code block with a copy button that lifts the rendered plain text
 *  straight off the DOM — so it copies the code, not the highlight markup.
 *  `node` (react-markdown's hast element) is pulled out so it isn't spread
 *  onto the DOM <pre> as an invalid attribute. */
function CodeBlock({ children, node: _node, ...rest }: ComponentPropsWithoutRef<'pre'> & ExtraProps) {
  const preRef = useRef<HTMLPreElement>(null)
  const [copied, setCopied] = useState(false)
  const timerRef = useRef<number | undefined>(undefined)
  useEffect(() => () => window.clearTimeout(timerRef.current), [])
  return (
    <div className="code-block">
      <button
        className={`icon-btn copy-btn code-copy ${copied ? 'copied' : ''}`}
        title="Copy code"
        aria-label={copied ? 'Copied' : 'Copy code'}
        onClick={async () => {
          const text = preRef.current?.textContent ?? ''
          if (await copyToClipboard(text)) {
            setCopied(true)
            window.clearTimeout(timerRef.current)
            timerRef.current = window.setTimeout(() => setCopied(false), 1500)
          }
        }}
      >
        {copied ? '✓' : '⧉'}
      </button>
      <pre ref={preRef} {...rest}>
        {children}
      </pre>
    </div>
  )
}

export default function Markdown({ children }: { children: string }) {
  return (
    <div className="markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeHighlight]}
        components={{ pre: CodeBlock }}
      >
        {children}
      </ReactMarkdown>
    </div>
  )
}
