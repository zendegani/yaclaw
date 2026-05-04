// Voice helpers for the Chat composer.
//
// `startRecording` returns a controller that stops the MediaRecorder and
// returns the captured Blob. The caller posts that to `/voice/...` to
// transcribe + dispatch through the same agent loop the textarea uses.
//
// `speakText` is a thin wrapper around the browser's SpeechSynthesis API
// — voice quality and language depend on the OS, but it's zero infra and
// works offline.

export interface RecorderController {
  stop(): Promise<Blob>;
  cancel(): void;
}

export async function startRecording(): Promise<RecorderController> {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  const mime = pickMime();
  const recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
  const chunks: Blob[] = [];
  recorder.ondataavailable = (e) => {
    if (e.data.size > 0) chunks.push(e.data);
  };
  recorder.start();

  const stopTracks = () => stream.getTracks().forEach((t) => t.stop());

  return {
    async stop(): Promise<Blob> {
      return new Promise<Blob>((resolve) => {
        recorder.onstop = () => {
          stopTracks();
          resolve(new Blob(chunks, { type: recorder.mimeType || "audio/webm" }));
        };
        recorder.stop();
      });
    },
    cancel(): void {
      try {
        recorder.stop();
      } catch {
        /* already stopped */
      }
      stopTracks();
    },
  };
}

function pickMime(): string | undefined {
  const candidates = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus"];
  for (const c of candidates) {
    if (typeof MediaRecorder !== "undefined" && MediaRecorder.isTypeSupported(c)) {
      return c;
    }
  }
  return undefined;
}

export async function uploadVoice(
  userId: string,
  sessionId: string,
  blob: Blob,
  messageId: string,
): Promise<{ transcript: string; message_id: string }> {
  const form = new FormData();
  form.append("audio", blob, "voice.webm");
  form.append("message_id", messageId);
  const res = await fetch(`/voice/${userId}/${sessionId}`, {
    method: "POST",
    body: form,
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`voice upload failed (${res.status}): ${detail}`);
  }
  return res.json();
}

export function speakText(text: string): void {
  if (typeof window === "undefined" || !window.speechSynthesis) return;
  const trimmed = text.trim();
  if (!trimmed) return;
  // Cancel any in-flight utterance so a new turn doesn't overlap with
  // the previous one.
  window.speechSynthesis.cancel();
  const utter = new SpeechSynthesisUtterance(trimmed);
  window.speechSynthesis.speak(utter);
}

export function cancelSpeech(): void {
  if (typeof window === "undefined" || !window.speechSynthesis) return;
  window.speechSynthesis.cancel();
}

const SPEAK_KEY = "pishkar.speak_replies";

export function getSpeakReplies(): boolean {
  return localStorage.getItem(SPEAK_KEY) === "1";
}

export function setSpeakReplies(value: boolean): void {
  localStorage.setItem(SPEAK_KEY, value ? "1" : "0");
}
