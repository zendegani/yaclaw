import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";

interface Props {
  open: boolean;
  tool: string;
  input: Record<string, unknown>;
  onAnswer: (answer: "ask_me" | "allow_once" | "allow_session") => void;
}

// Placeholder UI; the server-side approval gate event isn't wired through
// the WebSocket yet. Kept so the seam exists in the new layout.
export function ApprovalDialog({ open, tool, input, onAnswer }: Props) {
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onAnswer("ask_me")}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Approve tool call?</DialogTitle>
          <DialogDescription>
            Pishkar wants to run <span className="font-mono">{tool}</span>.
          </DialogDescription>
        </DialogHeader>
        <pre className="overflow-x-auto rounded bg-muted p-2 text-xs">
          {JSON.stringify(input, null, 2)}
        </pre>
        <DialogFooter className="gap-2 sm:gap-2">
          <Button variant="ghost" onClick={() => onAnswer("ask_me")}>Ask me</Button>
          <Button variant="secondary" onClick={() => onAnswer("allow_once")}>Allow once</Button>
          <Button onClick={() => onAnswer("allow_session")}>Allow this session</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
