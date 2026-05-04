import { useState } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { getSpeakReplies, setSpeakReplies, cancelSpeech } from "@/api/voice";

export function SettingsPage() {
  const [speak, setSpeak] = useState<boolean>(getSpeakReplies());

  const onToggleSpeak = (next: boolean) => {
    setSpeak(next);
    setSpeakReplies(next);
    if (!next) cancelSpeech();
  };

  return (
    <div className="h-full overflow-y-auto px-6 py-6">
      <h1 className="mb-1 text-lg font-semibold">Settings</h1>
      <p className="mb-6 text-sm text-muted-foreground">
        Pishkar treats the workspace as the source of truth for configuration. Edit the markdown files
        under <code className="rounded bg-muted px-1 py-0.5 text-xs">~/.pishkar/users/&lt;user&gt;/</code> to adjust persona,
        identity, and standing tasks.
      </p>
      <Card className="mb-6">
        <CardHeader>
          <CardTitle className="text-base">Voice</CardTitle>
          <CardDescription>
            Speak Pishkar's replies aloud using your browser's built-in
            speech engine. Quality depends on your OS voices; configure
            them in your system settings.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <label className="flex cursor-pointer items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={speak}
              onChange={(e) => onToggleSpeak(e.target.checked)}
            />
            Speak replies
          </label>
        </CardContent>
      </Card>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">SOUL.md</CardTitle>
            <CardDescription>Pishkar's persona, voice, and working style.</CardDescription>
          </CardHeader>
          <CardContent className="text-sm text-muted-foreground">
            Editable in any text editor. Reloaded on every turn.
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-base">USER.md</CardTitle>
            <CardDescription>What Pishkar should know about you.</CardDescription>
          </CardHeader>
          <CardContent className="text-sm text-muted-foreground">
            Pishkar can update this file via tool calls when you tell it new things.
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-base">HEARTBEAT.md</CardTitle>
            <CardDescription>Recurring tasks executed on a schedule.</CardDescription>
          </CardHeader>
          <CardContent className="text-sm text-muted-foreground">
            Driven by the heartbeat trigger. Empty by default.
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-base">AGENTS.md</CardTitle>
            <CardDescription>Sub-agents Pishkar can invoke.</CardDescription>
          </CardHeader>
          <CardContent className="text-sm text-muted-foreground">
            Reserved for future use.
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
