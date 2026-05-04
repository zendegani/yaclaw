import { useEffect, useRef, useState } from "react";
import { Send, Mic, Square } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useChatState, appendUserMessage } from "@/state/store";
import { useSocket } from "@/App";
import { ToolCall } from "@/components/ToolCall";
import { Thinking } from "@/components/Thinking";
import { Inspector } from "@/components/Inspector";
import { cn } from "@/lib/utils";
import { getSessionId, getUserId } from "@/App";
import {
  RecorderController,
  cancelSpeech,
  getSpeakReplies,
  speakText,
  startRecording,
  uploadVoice,
} from "@/api/voice";

export function ChatPage() {
  const socket = useSocket();
  const { items, status } = useChatState();
  const [draft, setDraft] = useState("");
  const [recording, setRecording] = useState(false);
  const [voiceBusy, setVoiceBusy] = useState(false);
  const [voiceError, setVoiceError] = useState<string | null>(null);
  const recorderRef = useRef<RecorderController | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const spokenRef = useRef<Set<string>>(new Set());
  // Set when the user sends voice, so the next assistant turn is spoken
  // even if "Speak replies" is off — mirrors Telegram's voice-in/voice-out
  // modality. Cleared after the first turn it triggers.
  const speakNextRef = useRef(false);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [items]);

  useEffect(() => {
    const last = items[items.length - 1];
    if (
      !last
      || last.kind !== "assistant"
      || !last.turn.done
      || !last.turn.text.trim()
    ) {
      return;
    }
    if (spokenRef.current.has(last.turn.turn_id)) return;
    const oneShot = speakNextRef.current;
    if (!getSpeakReplies() && !oneShot) return;
    spokenRef.current.add(last.turn.turn_id);
    speakNextRef.current = false;
    speakText(last.turn.text);
  }, [items]);

  useEffect(() => {
    return () => cancelSpeech();
  }, []);

  const send = () => {
    const trimmed = draft.trim();
    if (!trimmed) return;
    const messageId = crypto.randomUUID();
    socket.send(trimmed, messageId);
    appendUserMessage(trimmed, messageId);
    setDraft("");
  };

  const toggleRecording = async () => {
    setVoiceError(null);
    if (recording) {
      const ctrl = recorderRef.current;
      recorderRef.current = null;
      setRecording(false);
      if (!ctrl) return;
      setVoiceBusy(true);
      const messageId = crypto.randomUUID();
      try {
        const blob = await ctrl.stop();
        if (blob.size === 0) {
          setVoiceError("Empty recording.");
          return;
        }
        const { transcript } = await uploadVoice(
          getUserId(),
          getSessionId(),
          blob,
          messageId,
        );
        // Render the transcript optimistically. The server echoes a
        // user_message with the same message_id, which the store dedupes.
        appendUserMessage(transcript, messageId);
        // Mirror modality: the next assistant turn gets spoken aloud
        // regardless of the "Speak replies" toggle, then resets.
        speakNextRef.current = true;
      } catch (err) {
        setVoiceError(err instanceof Error ? err.message : "Voice upload failed.");
      } finally {
        setVoiceBusy(false);
      }
      return;
    }
    try {
      recorderRef.current = await startRecording();
      setRecording(true);
    } catch (err) {
      setVoiceError(
        err instanceof Error ? err.message : "Microphone access denied.",
      );
    }
  };

  return (
    <div className="flex h-full">
      <div className="flex flex-1 flex-col overflow-hidden">
        <div className="flex-1 overflow-y-auto">
          <div className="mx-auto flex max-w-3xl flex-col gap-4 px-6 py-6">
            {items.length === 0 && (
              <div className="mt-20 text-center text-sm text-muted-foreground">
                Start a conversation with Pishkar.
              </div>
            )}
            {items.map((item, i) =>
              item.kind === "user" ? (
                <div key={i} className="flex justify-end">
                  <div className="max-w-[80%] rounded-2xl bg-primary px-4 py-2 text-sm text-primary-foreground">
                    <pre className="whitespace-pre-wrap font-sans">{item.content}</pre>
                  </div>
                </div>
              ) : (
                <div key={i} className="flex flex-col gap-2">
                  <Thinking text={item.turn.thinking} />
                  {item.turn.text && (
                    <div className="rounded-2xl bg-muted px-4 py-2 text-sm">
                      <pre className="whitespace-pre-wrap font-sans">{item.turn.text}</pre>
                    </div>
                  )}
                  {item.turn.toolOrder.map((id) => {
                    const slot = item.turn.toolCalls[id];
                    return (
                      <ToolCall
                        key={id}
                        use={slot.use}
                        result={slot.result}
                        isError={slot.isError}
                      />
                    );
                  })}
                </div>
              ),
            )}
            <div ref={bottomRef} />
          </div>
        </div>
        <div className="border-t border-border bg-background p-4">
          <div className="mx-auto flex max-w-3xl items-end gap-2">
            <Textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  send();
                }
              }}
              placeholder="Message Pishkar…"
              rows={2}
              className={cn("resize-none", status !== "open" && "opacity-60")}
            />
            <Button
              variant={recording ? "destructive" : "outline"}
              onClick={toggleRecording}
              disabled={voiceBusy}
              title={recording ? "Stop and send" : "Record voice"}
            >
              {recording ? <Square className="h-4 w-4" /> : <Mic className="h-4 w-4" />}
            </Button>
            <Button onClick={send} disabled={status !== "open" || !draft.trim()}>
              <Send className="h-4 w-4" />
            </Button>
          </div>
          {(voiceBusy || voiceError) && (
            <div className="mx-auto mt-2 max-w-3xl text-xs text-muted-foreground">
              {voiceBusy ? "Transcribing…" : voiceError}
            </div>
          )}
        </div>
      </div>
      <Inspector />
    </div>
  );
}
