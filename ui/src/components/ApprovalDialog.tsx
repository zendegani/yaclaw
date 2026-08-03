import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import type { ApprovalDecision } from "@/api/events";
import type { PendingApproval } from "@/state/store";

interface Props {
  approval: PendingApproval | null;
  onAnswer: (decision: ApprovalDecision) => void;
}

function Headline({ tool, input }: { tool: string; input: Record<string, unknown> }) {
  if (tool === "bash" && typeof input.cmd === "string") {
    return (
      <pre className="max-h-40 overflow-auto rounded-sm bg-muted p-2 text-xs">
        <span className="text-muted-foreground">$ </span>
        {input.cmd}
      </pre>
    );
  }
  if (tool === "write_file" && typeof input.path === "string") {
    const content = typeof input.content === "string" ? input.content : "";
    return (
      <div className="space-y-1">
        <div className="font-mono text-xs">
          <span className="text-muted-foreground">path: </span>
          {input.path}
          <span className="text-muted-foreground"> ({content.length} chars)</span>
        </div>
      </div>
    );
  }
  if (tool === "http" && typeof input.url === "string") {
    const method = typeof input.method === "string" ? input.method : "GET";
    return (
      <div className="font-mono text-xs">
        <span className="text-muted-foreground">{method.toUpperCase()} </span>
        {input.url}
      </div>
    );
  }
  return null;
}

export function ApprovalDialog({ approval, onAnswer }: Props) {
  return (
    <Dialog open={approval !== null} onOpenChange={(o) => !o && approval && onAnswer("deny")}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Approve tool call?</DialogTitle>
          <DialogDescription>
            Pishkar wants to run{" "}
            <span className="font-mono">{approval?.tool_name}</span>.
          </DialogDescription>
        </DialogHeader>
        {approval && (
          <div className="space-y-2">
            <Headline tool={approval.tool_name} input={approval.input} />
            <details className="text-xs">
              <summary className="cursor-pointer text-muted-foreground">
                Full arguments
              </summary>
              <pre className="mt-1 max-h-64 overflow-auto rounded-sm bg-muted p-2">
                {JSON.stringify(approval.input, null, 2)}
              </pre>
            </details>
          </div>
        )}
        <DialogFooter className="gap-2 sm:gap-2">
          <Button variant="ghost" onClick={() => onAnswer("deny")}>
            Deny
          </Button>
          <Button variant="secondary" onClick={() => onAnswer("allow_once")}>
            Allow once
          </Button>
          <Button onClick={() => onAnswer("allow_session")}>
            Allow this session
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
