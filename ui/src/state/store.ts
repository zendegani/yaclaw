import { useSyncExternalStore } from "react";
import type { Event, ToolUseBlock } from "@/api/events";

export interface AssistantTurn {
  turn_id: string;
  text: string;
  thinking: string;
  toolCalls: Record<
    string,
    { use: ToolUseBlock; inputRaw?: string; result?: string; isError?: boolean }
  >;
  toolOrder: string[];
  blockIndexToCallId: Record<number, string>;
  done: boolean;
  stopReason?: string;
  inputTokens?: number;
  outputTokens?: number;
  model?: string;
  startedAt: number;
  endedAt?: number;
}

export interface UserMessage { kind: "user"; content: string; ts: number; message_id?: string }
export interface AssistantMessage { kind: "assistant"; turn: AssistantTurn }
export type ChatItem = UserMessage | AssistantMessage;

export interface RawEntry {
  ts: number;
  event: Event;
}

export interface PendingApproval {
  request_id: string;
  tool_name: string;
  input: Record<string, unknown>;
}

export interface SessionAvailable {
  session_id: string;
  source_channel: string;
  ts: number;
}

interface State {
  items: ChatItem[];
  status: "connecting" | "open" | "closed";
  raw: RawEntry[];
  totals: { inputTokens: number; outputTokens: number; turns: number };
  approvals: PendingApproval[];
  sessionAvailable: SessionAvailable | null;
}

let state: State = {
  items: [],
  status: "connecting",
  raw: [],
  totals: { inputTokens: 0, outputTokens: 0, turns: 0 },
  approvals: [],
  sessionAvailable: null,
};
const listeners = new Set<() => void>();

function setState(next: State): void {
  state = next;
  listeners.forEach((l) => l());
}

function findOrCreateTurn(items: ChatItem[], turn_id: string): [ChatItem[], AssistantTurn] {
  const last = items[items.length - 1];
  if (last && last.kind === "assistant" && last.turn.turn_id === turn_id) {
    return [items, last.turn];
  }
  const turn: AssistantTurn = {
    turn_id,
    text: "",
    thinking: "",
    toolCalls: {},
    toolOrder: [],
    blockIndexToCallId: {},
    done: false,
    startedAt: Date.now(),
  };
  return [[...items, { kind: "assistant", turn }], turn];
}

const RAW_LIMIT = 1000;

export function applyEvent(event: Event): void {
  const items = state.items;
  const raw = [...state.raw, { ts: Date.now(), event }].slice(-RAW_LIMIT);
  let totals = state.totals;

  switch (event.type) {
    case "turn_start": {
      const [next] = findOrCreateTurn(items, event.turn_id);
      setState({ ...state, items: next, raw, totals });
      return;
    }
    case "message_start": {
      const [next, turn] = findOrCreateTurn(items, event.turn_id);
      turn.model = event.model;
      setState({ ...state, items: [...next], raw, totals });
      return;
    }
    case "content_block_start": {
      if (event.content_block.type === "tool_use") {
        const block = event.content_block;
        const [next, turn] = findOrCreateTurn(items, event.turn_id);
        if (!turn.toolCalls[block.id]) {
          turn.toolCalls[block.id] = { use: block, inputRaw: "" };
          turn.toolOrder.push(block.id);
        }
        turn.blockIndexToCallId[event.index] = block.id;
        setState({ ...state, items: [...next], raw, totals });
      } else {
        setState({ ...state, raw, totals });
      }
      return;
    }
    case "content_block_delta": {
      const [next, turn] = findOrCreateTurn(items, event.turn_id);
      if (event.delta.type === "text_delta") turn.text += event.delta.text;
      else if (event.delta.type === "thinking_delta") turn.thinking += event.delta.thinking;
      else if (event.delta.type === "input_json_delta") {
        const callId = turn.blockIndexToCallId[event.index];
        const slot = callId ? turn.toolCalls[callId] : undefined;
        if (slot) {
          slot.inputRaw = (slot.inputRaw ?? "") + event.delta.partial_json;
          // Best-effort live parse: streamed JSON is often invalid until the
          // last delta. We only update `use.input` when it parses cleanly so
          // we never show garbage; the final value lands at content_block_stop.
          try {
            const parsed = JSON.parse(slot.inputRaw);
            if (parsed && typeof parsed === "object") {
              slot.use = { ...slot.use, input: parsed as Record<string, unknown> };
            }
          } catch {
            // partial JSON — keep accumulating.
          }
        }
      }
      setState({ ...state, items: [...next], raw, totals });
      return;
    }
    case "content_block_stop": {
      const [next, turn] = findOrCreateTurn(items, event.turn_id);
      const callId = turn.blockIndexToCallId[event.index];
      const slot = callId ? turn.toolCalls[callId] : undefined;
      if (slot && slot.inputRaw) {
        try {
          const parsed = JSON.parse(slot.inputRaw);
          if (parsed && typeof parsed === "object") {
            slot.use = { ...slot.use, input: parsed as Record<string, unknown> };
          }
        } catch {
          // Leave whatever we last successfully parsed.
        }
      }
      setState({ ...state, items: [...next], raw, totals });
      return;
    }
    case "tool_result": {
      const [next, turn] = findOrCreateTurn(items, event.turn_id);
      const slot = turn.toolCalls[event.tool_use_id];
      if (slot) {
        slot.result = event.content;
        slot.isError = event.is_error;
      }
      setState({ ...state, items: [...next], raw, totals });
      return;
    }
    case "message_delta": {
      const [next, turn] = findOrCreateTurn(items, event.turn_id);
      if (event.input_tokens != null) turn.inputTokens = (turn.inputTokens ?? 0) + event.input_tokens;
      if (event.output_tokens != null) turn.outputTokens = (turn.outputTokens ?? 0) + event.output_tokens;
      totals = {
        inputTokens: totals.inputTokens + (event.input_tokens ?? 0),
        outputTokens: totals.outputTokens + (event.output_tokens ?? 0),
        turns: totals.turns,
      };
      setState({ ...state, items: [...next], raw, totals });
      return;
    }
    case "user_message": {
      // Echoed by the server so a fresh client (or one reconnecting via
      // replay) sees the user's own messages. Drop the duplicate if we
      // already appended optimistically.
      const exists = items.some(
        (it) => it.kind === "user" && it.message_id === event.message_id,
      );
      if (exists) {
        setState({ ...state, raw, totals });
        return;
      }
      const next: ChatItem[] = [
        ...items,
        { kind: "user", content: event.content, ts: Date.now(), message_id: event.message_id },
      ];
      setState({ ...state, items: next, raw, totals });
      return;
    }
    case "session_changed": {
      // Pishkar's other channel just opened a new session. Surface the
      // option but don't auto-switch — the user might be mid-thought here.
      setState({
        ...state,
        raw,
        totals,
        sessionAvailable: {
          session_id: event.session_id,
          source_channel: event.source_channel,
          ts: Date.now(),
        },
      });
      return;
    }
    case "approval_request": {
      const approvals = [
        ...state.approvals,
        { request_id: event.request_id, tool_name: event.tool_name, input: event.input },
      ];
      setState({ ...state, approvals, raw, totals });
      return;
    }
    case "turn_end": {
      const [next, turn] = findOrCreateTurn(items, event.turn_id);
      turn.done = true;
      turn.stopReason = event.stop_reason;
      turn.endedAt = Date.now();
      totals = { ...totals, turns: totals.turns + 1 };
      setState({ ...state, items: [...next], raw, totals });
      return;
    }
    default:
      setState({ ...state, raw, totals });
      return;
  }
}

export function appendUserMessage(content: string, message_id?: string): void {
  setState({
    ...state,
    items: [...state.items, { kind: "user", content, ts: Date.now(), message_id }],
  });
}

export function setStatus(status: State["status"]): void {
  setState({ ...state, status });
}

export function clearRaw(): void {
  setState({ ...state, raw: [] });
}

export function dismissSessionAvailable(): void {
  setState({ ...state, sessionAvailable: null });
}

export function dismissApproval(request_id: string): void {
  setState({
    ...state,
    approvals: state.approvals.filter((a) => a.request_id !== request_id),
  });
}

function subscribe(l: () => void): () => void {
  listeners.add(l);
  return () => {
    listeners.delete(l);
  };
}

export function useChatState(): State {
  return useSyncExternalStore(subscribe, () => state, () => state);
}
