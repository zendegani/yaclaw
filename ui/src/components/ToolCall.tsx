import { CheckCircle2, ChevronRight, Loader2, Wrench, XCircle } from "lucide-react";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { ToolUseBlock } from "@/api/events";

interface Props {
  use: ToolUseBlock;
  result?: string;
  isError?: boolean;
}

export function ToolCall({ use, result, isError }: Props) {
  const pending = result === undefined;
  const Icon = pending ? Loader2 : isError ? XCircle : CheckCircle2;
  return (
    <Collapsible
      defaultOpen
      className={cn(
        "group rounded-md border bg-muted/40 px-3 py-2",
        isError && "border-destructive/40 bg-destructive/10",
      )}
    >
      <CollapsibleTrigger className="flex w-full items-center gap-2 text-sm">
        <ChevronRight className="h-3 w-3 transition-transform group-data-[state=open]:rotate-90" />
        <Wrench className="h-3.5 w-3.5 text-muted-foreground" />
        <span className="font-mono">{use.name}</span>
        <Badge variant={isError ? "destructive" : "secondary"} className="ml-auto gap-1 font-normal">
          <Icon className={cn("h-3 w-3", pending && "animate-spin")} />
          {pending ? "running" : isError ? "error" : "ok"}
        </Badge>
      </CollapsibleTrigger>
      <CollapsibleContent className="mt-2 space-y-2">
        <div>
          <div className="mb-1 text-[10px] uppercase tracking-wide text-muted-foreground">input</div>
          <pre className="overflow-x-auto rounded-sm bg-background/60 p-2 text-xs">
            {JSON.stringify(use.input, null, 2)}
          </pre>
        </div>
        {!pending && (
          <div>
            <div className="mb-1 text-[10px] uppercase tracking-wide text-muted-foreground">result</div>
            <pre className="max-h-64 overflow-auto rounded-sm bg-background/60 p-2 text-xs">{result}</pre>
          </div>
        )}
      </CollapsibleContent>
    </Collapsible>
  );
}
