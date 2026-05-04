import { useMemo, useState } from "react";
import { Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { useChatState, clearRaw } from "@/state/store";

const TYPES = [
  "turn_start",
  "message_start",
  "content_block_start",
  "content_block_delta",
  "content_block_stop",
  "message_delta",
  "message_stop",
  "tool_result",
  "turn_end",
];

export function TracePage() {
  const { raw } = useChatState();
  const [enabled, setEnabled] = useState<Record<string, boolean>>(
    () => Object.fromEntries(TYPES.map((t) => [t, t !== "content_block_delta"])),
  );
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  const filtered = useMemo(
    () => raw.filter((e) => enabled[e.event.type] ?? true),
    [raw, enabled],
  );

  const rowKey = (entry: { ts: number; event: { event_id?: string } }, i: number) =>
    entry.event.event_id ?? `${entry.ts}-${i}`;

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-border px-6 py-3">
        <div>
          <h1 className="text-lg font-semibold">Event trace</h1>
          <p className="text-xs text-muted-foreground">
            Live stream of WebSocket events from the server. Last {raw.length} captured.
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={clearRaw}>
          <Trash2 className="mr-1 h-3 w-3" /> Clear
        </Button>
      </div>
      <div className="flex flex-wrap gap-1 border-b border-border bg-muted/30 px-6 py-2">
        {TYPES.map((t) => (
          <button
            key={t}
            onClick={() => setEnabled((s) => ({ ...s, [t]: !s[t] }))}
            className="cursor-pointer"
          >
            <Badge
              variant={enabled[t] ? "default" : "secondary"}
              className="font-mono text-[10px] font-normal"
            >
              {t}
            </Badge>
          </button>
        ))}
      </div>
      <div className="flex-1 overflow-y-auto px-6 py-3 font-mono text-xs">
        {filtered.length === 0 && (
          <div className="py-12 text-center text-sm text-muted-foreground">
            No events yet. Send a message from the Chat page.
          </div>
        )}
        <ul className="space-y-1">
          {filtered.map((entry, i) => {
            const key = rowKey(entry, i);
            const isOpen = !!expanded[key];
            return (
              <li
                key={key}
                className="rounded border border-border/50 bg-background"
              >
                <button
                  type="button"
                  onClick={() =>
                    setExpanded((s) => ({ ...s, [key]: !s[key] }))
                  }
                  className="flex w-full cursor-pointer gap-2 px-2 py-1 text-left hover:bg-muted/50"
                >
                  <span className="shrink-0 text-muted-foreground">
                    {isOpen ? "▾" : "▸"}
                  </span>
                  <span className="shrink-0 text-muted-foreground">
                    {new Date(entry.ts).toLocaleTimeString()}
                  </span>
                  <span className="shrink-0 font-semibold text-primary">
                    {entry.event.type}
                  </span>
                  <span className="truncate text-muted-foreground">
                    {JSON.stringify(entry.event)}
                  </span>
                </button>
                {isOpen && (
                  <pre className="overflow-x-auto whitespace-pre-wrap break-all border-t border-border/50 bg-muted/30 px-3 py-2 text-[11px] leading-relaxed">
                    {JSON.stringify(entry.event, null, 2)}
                  </pre>
                )}
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}
