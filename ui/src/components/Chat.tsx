import { useEffect, useRef, useState } from "react";
import type { PishkarSocket } from "../api/socket";
import { useChatState, appendUserMessage } from "../state/store";
import { ToolCall } from "./ToolCall";
import { Thinking } from "./Thinking";

interface Props {
  socket: PishkarSocket;
}

export function Chat({ socket }: Props) {
  const { items, status } = useChatState();
  const [draft, setDraft] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [items]);

  const send = () => {
    const trimmed = draft.trim();
    if (!trimmed) return;
    socket.send(trimmed);
    appendUserMessage(trimmed);
    setDraft("");
  };

  return (
    <div className="chat">
      <header className={`status ${status}`}>● {status}</header>
      <div className="messages">
        {items.map((item, i) =>
          item.kind === "user" ? (
            <div key={i} className="msg user"><pre>{item.content}</pre></div>
          ) : (
            <div key={i} className="msg assistant">
              <Thinking text={item.turn.thinking} />
              {item.turn.text && <pre>{item.turn.text}</pre>}
              {item.turn.toolOrder.map((id) => {
                const slot = item.turn.toolCalls[id];
                return <ToolCall key={id} use={slot.use} result={slot.result} isError={slot.isError} />;
              })}
            </div>
          ),
        )}
        <div ref={bottomRef} />
      </div>
      <div className="composer">
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
          placeholder="Message Pishkar…"
        />
        <button onClick={send} disabled={status !== "open"}>Send</button>
      </div>
    </div>
  );
}
