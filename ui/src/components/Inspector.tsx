import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { useChatState } from "@/state/store";

function formatDuration(start: number, end?: number): string {
  if (!end) return "…";
  const ms = end - start;
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(2)}s`;
}

export function Inspector() {
  const { items, totals } = useChatState();
  const lastTurn = [...items].reverse().find((i) => i.kind === "assistant");
  const turn = lastTurn?.kind === "assistant" ? lastTurn.turn : undefined;

  return (
    <div className="flex h-full w-80 shrink-0 flex-col gap-3 overflow-y-auto border-l border-border bg-muted/20 p-3">
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">Session totals</CardTitle>
        </CardHeader>
        <CardContent className="space-y-1.5 text-xs">
          <Row label="Turns" value={totals.turns.toString()} />
          <Row label="Input tokens" value={totals.inputTokens.toLocaleString()} />
          <Row label="Output tokens" value={totals.outputTokens.toLocaleString()} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">Last turn</CardTitle>
        </CardHeader>
        <CardContent className="space-y-1.5 text-xs">
          {turn ? (
            <>
              <Row label="Model" value={turn.model ?? "—"} mono />
              <Row label="Status" value={
                <Badge variant={turn.done ? "secondary" : "default"} className="font-normal">
                  {turn.done ? turn.stopReason ?? "done" : "running"}
                </Badge>
              } />
              <Row label="Duration" value={formatDuration(turn.startedAt, turn.endedAt)} />
              <Row label="In" value={(turn.inputTokens ?? 0).toString()} />
              <Row label="Out" value={(turn.outputTokens ?? 0).toString()} />
              {turn.toolOrder.length > 0 && (
                <>
                  <Separator className="my-2" />
                  <div className="text-[10px] uppercase tracking-wide text-muted-foreground">
                    Tool calls
                  </div>
                  <ul className="space-y-1">
                    {turn.toolOrder.map((id) => {
                      const slot = turn.toolCalls[id];
                      const status = slot.result === undefined
                        ? "running"
                        : slot.isError ? "error" : "ok";
                      return (
                        <li key={id} className="flex items-center justify-between gap-2">
                          <span className="truncate font-mono">{slot.use.name}</span>
                          <Badge
                            variant={status === "error" ? "destructive" : "secondary"}
                            className="font-normal"
                          >
                            {status}
                          </Badge>
                        </li>
                      );
                    })}
                  </ul>
                </>
              )}
            </>
          ) : (
            <div className="text-muted-foreground">No turns yet.</div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function Row({ label, value, mono }: { label: string; value: React.ReactNode; mono?: boolean }) {
  return (
    <div className="flex items-center justify-between gap-2">
      <span className="text-muted-foreground">{label}</span>
      <span className={mono ? "font-mono" : undefined}>{value}</span>
    </div>
  );
}
