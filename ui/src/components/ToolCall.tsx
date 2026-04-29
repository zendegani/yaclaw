import type { ToolUseBlock } from "../api/events";

interface Props {
  use: ToolUseBlock;
  result?: string;
  isError?: boolean;
}

export function ToolCall({ use, result, isError }: Props) {
  return (
    <div className={`tool-call ${isError ? "error" : ""}`}>
      <div className="tool-name">⚙ {use.name}</div>
      <pre className="tool-input">{JSON.stringify(use.input, null, 2)}</pre>
      {result === undefined ? (
        <div className="tool-pending">running…</div>
      ) : (
        <pre className="tool-result">{result}</pre>
      )}
    </div>
  );
}
