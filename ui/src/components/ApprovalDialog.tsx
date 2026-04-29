interface Props {
  tool: string;
  input: Record<string, unknown>;
  onAnswer: (answer: "ask_me" | "allow_once" | "allow_session") => void;
}

// Placeholder until the server-side approval gate (item 9) emits its
// request event. The three answers match `Ask Me / Allow Once / Allow All`.
export function ApprovalDialog({ tool, input, onAnswer }: Props) {
  return (
    <div className="approval-dialog">
      <h3>Approve tool call?</h3>
      <div><b>{tool}</b></div>
      <pre>{JSON.stringify(input, null, 2)}</pre>
      <div className="approval-buttons">
        <button onClick={() => onAnswer("ask_me")}>Ask Me</button>
        <button onClick={() => onAnswer("allow_once")}>Allow Once</button>
        <button onClick={() => onAnswer("allow_session")}>Allow All This Session</button>
      </div>
    </div>
  );
}
