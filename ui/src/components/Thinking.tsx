interface Props {
  text: string;
}

export function Thinking({ text }: Props) {
  if (!text) return null;
  return (
    <details className="thinking">
      <summary>thinking</summary>
      <pre>{text}</pre>
    </details>
  );
}
