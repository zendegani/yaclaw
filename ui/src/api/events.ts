// Hand-mirrored from pishkar/core/events.py.
// Regenerate via `uv run python scripts/codegen_events_ts.py` when models change.

export interface EventBase {
  event_id: string;
  turn_id: string;
  session_id: string;
  timestamp: string;
}

export interface TextBlock { type: "text"; text: string }
export interface ToolUseBlock { type: "tool_use"; id: string; name: string; input: Record<string, unknown> }
export interface ThinkingBlock { type: "thinking"; thinking: string }
export type ContentBlock = TextBlock | ToolUseBlock | ThinkingBlock;

export interface TextDelta { type: "text_delta"; text: string }
export interface InputJsonDelta { type: "input_json_delta"; partial_json: string }
export interface ThinkingDelta { type: "thinking_delta"; thinking: string }
export type ContentDelta = TextDelta | InputJsonDelta | ThinkingDelta;

export type StopReason = "end_turn" | "tool_use" | "max_tokens" | "stop_sequence";
export type TurnStopReason = "end_turn" | "max_turns" | "loop_detected" | "error";

export interface TurnStart extends EventBase { type: "turn_start"; turn_index: number }
export interface MessageStart extends EventBase { type: "message_start"; role: "assistant"; model: string }
export interface ContentBlockStart extends EventBase { type: "content_block_start"; index: number; content_block: ContentBlock }
export interface ContentBlockDelta extends EventBase { type: "content_block_delta"; index: number; delta: ContentDelta }
export interface ContentBlockStop extends EventBase { type: "content_block_stop"; index: number }
export interface MessageDelta extends EventBase {
  type: "message_delta";
  stop_reason: StopReason | null;
  input_tokens: number | null;
  output_tokens: number | null;
}
export interface MessageStop extends EventBase { type: "message_stop" }
export interface ToolResult extends EventBase {
  type: "tool_result";
  tool_use_id: string;
  content: string;
  is_error: boolean;
}
export interface TurnEnd extends EventBase { type: "turn_end"; stop_reason: TurnStopReason }
export interface ApprovalRequest extends EventBase {
  type: "approval_request";
  request_id: string;
  tool_name: string;
  input: Record<string, unknown>;
}
export interface UserMessageEvent extends EventBase {
  type: "user_message";
  message_id: string;
  content: string;
}

export type Event =
  | TurnStart
  | MessageStart
  | ContentBlockStart
  | ContentBlockDelta
  | ContentBlockStop
  | MessageDelta
  | MessageStop
  | ToolResult
  | TurnEnd
  | ApprovalRequest
  | UserMessageEvent;

export type ApprovalDecision = "allow_once" | "allow_session" | "deny";

export interface ApprovalResponse {
  type: "approval_response";
  request_id: string;
  decision: ApprovalDecision;
}

export interface InboundMessage {
  message_id?: string;
  user_id?: string;
  session_id?: string;
  channel?: string;
  content: string;
  metadata?: Record<string, unknown>;
}
