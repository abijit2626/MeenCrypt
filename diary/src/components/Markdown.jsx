import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

// Raw HTML in entries is rendered as text (react-markdown's default), so
// decrypted content can never inject markup or scripts.
export default function Markdown({ children }) {
  return (
    <div className="markdown">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{children}</ReactMarkdown>
    </div>
  )
}
