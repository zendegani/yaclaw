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
          <pre className="max-h-64 overflow-auto rounded bg-muted p-2 text-xs">
            {JSON.stringify(approval.input, null, 2)}
          </pre>
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
