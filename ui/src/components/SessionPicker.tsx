import { useEffect, useState } from "react";
import { ChevronDown } from "lucide-react";
import { Button } from "@/components/ui/button";
import { getSessionId, getUserId, switchSession } from "@/App";

interface SessionRow {
  session_id: string;
  created_at: string;
  last_activity: string;
  message_count: number;
  last_channel: string | null;
}

function fmtRelative(iso: string): string {
  const t = new Date(iso).getTime();
  const diffSec = Math.max(0, Math.round((Date.now() - t) / 1000));
  if (diffSec < 60) return `${diffSec}s ago`;
  if (diffSec < 3600) return `${Math.round(diffSec / 60)}m ago`;
  if (diffSec < 86400) return `${Math.round(diffSec / 3600)}h ago`;
  return `${Math.round(diffSec / 86400)}d ago`;
}

export function SessionPicker() {
  const [open, setOpen] = useState(false);
  const [rows, setRows] = useState<SessionRow[]>([]);
  const current = getSessionId();

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    fetch(`/sessions/${getUserId()}`)
      .then((r) => r.json())
      .then((d: { sessions: SessionRow[] }) => {
        if (!cancelled) setRows(d.sessions ?? []);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [open]);

  return (
    <div className="relative">
      <Button
        variant="ghost"
        size="sm"
        className="h-7 w-full justify-between gap-2 px-2 text-xs"
        onClick={() => setOpen((o) => !o)}
      >
        <span className="truncate font-mono">
          session: {current ? current.slice(0, 8) : "—"}
        </span>
        <ChevronDown className="h-3 w-3 shrink-0" />
      </Button>
      {open && (
        <div className="absolute left-0 right-0 z-20 mt-1 max-h-72 overflow-auto rounded-md border bg-popover text-popover-foreground shadow-md">
          {rows.length === 0 && (
            <div className="px-3 py-2 text-xs text-muted-foreground">No sessions yet.</div>
          )}
          {rows.map((row) => {
            const isCurrent = row.session_id === current;
            return (
              <button
                key={row.session_id}
                type="button"
                disabled={isCurrent}
                onClick={() => switchSession(row.session_id)}
                className={
                  "block w-full px-3 py-2 text-left text-xs hover:bg-accent hover:text-accent-foreground disabled:opacity-50 disabled:hover:bg-transparent"
                }
              >
                <div className="flex items-center justify-between font-mono">
                  <span>{row.session_id.slice(0, 8)}</span>
                  <span className="text-muted-foreground">
                    {fmtRelative(row.last_activity)}
                  </span>
                </div>
                <div className="text-[10px] text-muted-foreground">
                  {row.message_count} msgs · {row.last_channel ?? "—"}
                  {isCurrent && " · current"}
                </div>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
