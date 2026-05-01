import { X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useChatState, dismissSessionAvailable } from "@/state/store";
import { switchSession } from "@/App";

export function SessionAvailableBanner() {
  const { sessionAvailable } = useChatState();
  if (!sessionAvailable) return null;
  return (
    <div className="flex items-center gap-3 border-b border-amber-500/30 bg-amber-500/10 px-4 py-2 text-sm">
      <span className="flex-1">
        <span className="font-medium capitalize">{sessionAvailable.source_channel}</span>{" "}
        started a new session{" "}
        <span className="font-mono text-xs">
          ({sessionAvailable.session_id.slice(0, 8)})
        </span>
        .
      </span>
      <Button
        size="sm"
        variant="secondary"
        onClick={() => switchSession(sessionAvailable.session_id)}
      >
        Switch
      </Button>
      <Button
        size="sm"
        variant="ghost"
        className="h-8 w-8 p-0"
        onClick={dismissSessionAvailable}
      >
        <X className="h-4 w-4" />
      </Button>
    </div>
  );
}
