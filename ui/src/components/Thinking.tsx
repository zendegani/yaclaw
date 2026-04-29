import { Brain, ChevronRight } from "lucide-react";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";

export function Thinking({ text }: { text: string }) {
  if (!text) return null;
  return (
    <Collapsible className="group rounded-md border border-border/60 bg-muted/30 px-3 py-2">
      <CollapsibleTrigger className="flex w-full items-center gap-2 text-xs text-muted-foreground">
        <ChevronRight className="h-3 w-3 transition-transform group-data-[state=open]:rotate-90" />
        <Brain className="h-3 w-3" />
        thinking
      </CollapsibleTrigger>
      <CollapsibleContent className="mt-2">
        <pre className="whitespace-pre-wrap text-xs leading-relaxed text-muted-foreground">{text}</pre>
      </CollapsibleContent>
    </Collapsible>
  );
}
