import { useEffect, useRef, useState } from "react";
import { Send } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useChatState, appendUserMessage } from "@/state/store";
import { useSocket } from "@/App";
import { ToolCall } from "@/components/ToolCall";
import { Thinking } from "@/components/Thinking";
import { Inspector } from "@/components/Inspector";
import { cn } from "@/lib/utils";

export function ChatPage() {
  const socket = useSocket();
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
    <div className="flex h-full">
      <div className="flex flex-1 flex-col overflow-hidden">
        <div className="flex-1 overflow-y-auto">
          <div className="mx-auto flex max-w-3xl flex-col gap-4 px-6 py-6">
            {items.length === 0 && (
              <div className="mt-20 text-center text-sm text-muted-foreground">
                Start a conversation with Pishkar.
              </div>
            )}
            {items.map((item, i) =>
              item.kind === "user" ? (
                <div key={i} className="flex justify-end">
                  <div className="max-w-[80%] rounded-2xl bg-primary px-4 py-2 text-sm text-primary-foreground">
                    <pre className="whitespace-pre-wrap font-sans">{item.content}</pre>
                  </div>
                </div>
              ) : (
                <div key={i} className="flex flex-col gap-2">
                  <Thinking text={item.turn.thinking} />
                  {item.turn.text && (
                    <div className="rounded-2xl bg-muted px-4 py-2 text-sm">
                      <pre className="whitespace-pre-wrap font-sans">{item.turn.text}</pre>
                    </div>
                  )}
                  {item.turn.toolOrder.map((id) => {
                    const slot = item.turn.toolCalls[id];
                    return (
                      <ToolCall
                        key={id}
                        use={slot.use}
                        result={slot.result}
                        isError={slot.isError}
                      />
                    );
                  })}
                </div>
              ),
            )}
            <div ref={bottomRef} />
          </div>
        </div>
        <div className="border-t border-border bg-background p-4">
          <div className="mx-auto flex max-w-3xl items-end gap-2">
            <Textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  send();
                }
              }}
              placeholder="Message Pishkar…"
              rows={2}
              className={cn("resize-none", status !== "open" && "opacity-60")}
            />
            <Button onClick={send} disabled={status !== "open" || !draft.trim()}>
              <Send className="h-4 w-4" />
            </Button>
          </div>
        </div>
      </div>
      <Inspector />
    </div>
  );
}
