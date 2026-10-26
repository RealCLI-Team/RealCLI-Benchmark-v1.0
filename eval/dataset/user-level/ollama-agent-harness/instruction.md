# Turn the Experimental Agent Prototype into a Reusable Agent Harness Core

We build agent-style products on top of ollama. The repo `/app` (the ollama source) currently contains an experimental prototype: `ollama run --experimental` (along with the two switches `--experimental-yolo` and `--experimental-websearch`) enters the interactive agent loop under `x/`. This prototype validated several judgments: a model-conversation + tool-call loop is viable; bash / web tools need approval and output truncation; long tasks blow up the context. But it hardwires the conversation loop, approval, and tools into the CLI interaction layer, so the newly formed TUI team can't reuse any of it; it's also missing a session event mechanism, working-directory tracking, and context compaction.

Sink this capability down into a reusable library package for the TUI to import directly. This task delivers only the harness core — no terminal UI. Before you start, thoroughly read the behavior of the prototype under `x/` (tool output formats, truncation strategy, approval interaction, rough token estimation, etc.) — much of the external behavior must follow from it; the existing related routes on the server side (see the experimental web endpoints registered in `/app/server/routes.go`) must also be aligned with.

## Deliverables

1. New package `agent` (the module root path is the module line in `/app/go.mod`; the `agent`, `agent/tools`, and `api` below are all relative to that module): session run loop, tool registry, event mechanism, approval model, context compaction.
2. New subpackage `agent/tools`: built-in tools bash / read / edit / web_search / web_fetch.
3. Two new client methods with companion types in the `api` package, calling the local server's experimental web endpoints.
4. Retire the old experimental entry points: the three flags `--experimental`, `--experimental-yolo`, `--experimental-websearch` on `ollama run` go away; how you clean up the prototype implementation under `x/` is up to you, but `go build ./...` must stay clean and no second agent-loop entry point may remain on the CLI.

## Public API Contract (finalized by the TUI team — follow it verbatim; private in-package implementation is free)

### agent package: sessions and running

```go
// 对话客户端抽象；*api.Client 天然满足它。
type ChatClient interface {
    Chat(context.Context, *api.ChatRequest, api.ChatResponseFunc) error
}

type Session struct {
    Client           ChatClient
    EventSinks       []EventSink
    Tools            *Registry
    ApprovalPrompter ApprovalPrompter
    AllowAllTools    bool
    WorkingDir       string
    Compactor        Compactor
}

type RunOptions struct {
    ChatID        string
    Model         string
    SystemPrompt  string
    Messages      []api.Message
    NewMessages   []api.Message
    Format        string
    Options       map[string]any
    Think         *api.ThinkValue
    KeepAlive     *api.Duration
    MaxToolRounds int // 0=默认 100；负数=不限
}

type RunResult struct {
    Messages   []api.Message
    Latest     api.ChatResponse
    WorkingDir string
}

func (s *Session) Run(ctx context.Context, opts RunOptions) (*RunResult, error)
```

### agent package: events (the TUI renders from these; the event-type values and JSON field names are wire protocol)

Event-type values: `message_delta`, `thinking_delta`, `tool_call_detected`, `tool_started`, `tool_finished`, `compaction_started`, `compaction_progress`, `compacted`, `compaction_skipped`, `run_finished`, `error` (each has a corresponding exported constant like `Event<MessageDelta>`, e.g. `EventMessageDelta EventType = "message_delta"`).

```go
type Event struct {
    Type       EventType      `json:"type"`
    RunID      string         `json:"runId,omitempty"`
    ChatID     string         `json:"chatId,omitempty"`
    Model      string         `json:"model,omitempty"`
    Status     string         `json:"status,omitempty"`
    ToolCallID string         `json:"toolCallId,omitempty"`
    ToolName   string         `json:"toolName,omitempty"`
    WorkingDir string         `json:"workingDir,omitempty"`
    Content    string         `json:"content,omitempty"`
    Thinking   string         `json:"thinking,omitempty"`
    ToolCalls  []api.ToolCall `json:"toolCalls,omitempty"`
    Messages   []api.Message  `json:"messages,omitempty"`
    Args       map[string]any `json:"args,omitempty"`
    Tokens     int            `json:"tokens,omitempty"`
    Error      string         `json:"error,omitempty"`
}

type EventSink interface { Emit(Event) error }
type EventSinkFunc func(Event) error // 实现 EventSink
```

A single `Run` must have one non-empty RunID that persists throughout, carried by all events along with ChatID and Model.

### agent package: tool registry

```go
type ToolContext struct { WorkingDir string }
type ToolResult  struct { Content string; WorkingDir string }

type Tool interface {
    Name() string
    Description() string
    Schema() api.ToolFunction
    Execute(context.Context, ToolContext, map[string]any) (ToolResult, error)
}

type ApprovalRequired interface { RequiresApproval(map[string]any) bool }

type Registry struct { /* 零值即可用 */ }
func (r *Registry) Register(tool Tool)
func (r *Registry) Get(name string) (Tool, bool)
func (r *Registry) Names() []string   // 字典序
func (r *Registry) Tools() api.Tools  // 按名字排序；每项 Type 恒为 "function"，Function 为工具 Schema
func (r *Registry) Execute(ctx context.Context, toolCtx ToolContext, call api.ToolCall) (ToolResult, error) // 未知名报错 "unknown tool: <name>"
func ToolRequiresApproval(tool Tool, args map[string]any) bool // 未实现 ApprovalRequired 的视为不需要审批
```

### agent package: approval

```go
type ApprovalRequest struct {
    WorkingDir string
    Calls      []ApprovalToolCall
}
type ApprovalToolCall struct {
    ToolCallID string
    ToolName   string
    Args       map[string]any
}
type Approval struct {
    Allow    bool
    AllowAll bool
    Reason   string
}
type ApprovalPrompter interface {
    PromptApproval(context.Context, ApprovalRequest) (Approval, error)
}
```

### agent package: context compaction

```go
type Compactor interface {
    MaybeCompact(context.Context, CompactionRequest) (CompactionResult, error)
}

type CompactionOptions struct {
    ContextWindowTokens int     // 默认 32768
    KeepUserTurns       int     // 默认保留最近 3 个 user 轮；窗口 < 16000 token 时强制为 0
    Threshold           float64 // 默认 0.8
}

type CompactionRequest struct {
    ChatID        string
    Model         string
    SystemPrompt  string
    Messages      []api.Message
    Tools         api.Tools
    Format        string
    Latest        api.ChatResponse
    Options       map[string]any
    KeepAlive     *api.Duration
    Think         *api.ThinkValue
    Force         bool
    ContinueTask  bool
    KeepUserTurns *int
    Progress      func(CompactionProgress)
}

type CompactionProgress struct { Tokens int }

type CompactionResult struct {
    Messages  []api.Message
    Compacted bool
    Due       bool
    Summary   string
    Reason    string
}

type SimpleCompactor struct {
    Client  ChatClient
    Options CompactionOptions
}
```

The representation of a compaction in the message history is the single canonical format for the whole repo (both the TUI and later compactions rely on it for recognition):

```go
const (
    CompactionSummaryMessagePrefix = "Conversation summary:\n"
    CompactionToolName             = "summary"
    CompactionToolCallID           = "ollama_compaction"
    CompactionContinueInstruction  = "continue the task in progress. the history has been compacted, do not mention compaction to the user"
)

func CompactionSummaryMessages(summary string, continueTask bool) []api.Message
func CompactionSummaryText(content string) string
func CompactionSummaryContent(msg api.Message) (string, bool)
func IsCompactionSummary(msg api.Message) bool
func IsCompactionToolResult(msg api.Message) bool
func IsCompactionToolCall(msg api.Message) bool
func ResolveContextWindowTokens(options map[string]any, configured int) int // options["num_ctx"] 优先于 configured
func ResolveCompactionThreshold(configured float64) float64
```

### api package additions

The server already registers `POST /api/experimental/web_search` and `POST /api/experimental/web_fetch`; the client needs the corresponding methods:

```go
type WebSearchRequest struct {
    Query      string `json:"query"`
    MaxResults int    `json:"max_results,omitempty"`
}
type WebSearchResult struct {
    Title   string `json:"title"`
    URL     string `json:"url"`
    Content string `json:"content"`
}
type WebSearchResponse struct {
    Results []WebSearchResult `json:"results"`
}
type WebFetchRequest struct {
    URL string `json:"url"`
}
type WebFetchResponse struct {
    Title   string   `json:"title"`
    Content string   `json:"content"`
    Links   []string `json:"links,omitempty"`
}

func (c *Client) WebSearchExperimental(ctx context.Context, req *WebSearchRequest) (*WebSearchResponse, error)
func (c *Client) WebFetchExperimental(ctx context.Context, req *WebFetchRequest) (*WebFetchResponse, error)
```

### agent/tools package

Export the five tool types `Bash`, `Read`, `Edit`, `WebSearch`, `WebFetch` (all usable as zero-value structs); their `Name()` are `bash`, `read`, `edit`, `web_search`, `web_fetch` respectively; all satisfy `agent.Tool` and `RequiresApproval` is always true. Required schema parameters: bash→`command`, read→`path`, edit→`path`/`old_text`/`new_text`, web_search→`query`, web_fetch→`url`. When the server demands authentication, the web tools must return the sentinel errors `ErrWebSearchAuthRequired` ("web search requires authentication") / `ErrWebFetchAuthRequired` ("web fetch requires authentication"), which callers detect with `errors.Is`.

## Run-Loop Semantics

- Argument validation: nil session, nil Client, and empty Model each raise an error (the latter two with the messages `agent session requires a chat client` / `agent session requires a model`).
- History = `Messages` + `NewMessages` concatenated in order. On each model request: inject `SystemPrompt` as the first system message when non-empty; attach tools when the registry has any; when `Format == "json"`, put the JSON string `"json"` into `api.ChatRequest.Format` (it's `json.RawMessage`; any other non-empty format passes through as-is); `Options`/`Think`/`KeepAlive` pass through.
- Streaming aggregation: content and thinking deltas append into the assistant message, emitting `message_delta` / `thinking_delta` per delta respectively; tool_calls are aggregated and emit `tool_call_detected` (carrying this batch's ToolCalls); empty response frames only update Latest. Assistant messages enter history only when non-empty.
- Pre-flight budget check: rough-estimate tokens at about 4 runes ≈ 1 token (same as the prototype's measure); when the estimated prompt ≥ the context window, fail directly with the message prefix `prompt is too large for the current context (~<估算>/<窗口> tokens).` and emit an `error` event.
- When the model returns 5xx (`api.StatusError.StatusCode >= 500`): re-inject `Your previous response caused an error: <错误>\n\nPlease try again with a valid response.` as a user message and retry; at most 2 retries for consecutive 5xx, and the 3rd time the error is thrown to the caller; non-5xx errors fail immediately and emit an `error` event.
- When the model returns tool calls, enter a tool round: first run unified approval (see below), then execute one by one after approval; each call emits `tool_started` (Status `running`, carrying WorkingDir and Args), and `tool_finished` when done (Status `done`/`failed`/`denied`/`skipped`). Each result is appended to history as a `role="tool"` message (with ToolName, ToolCallID); after the whole batch completes, return to the model round.
- Unknown tools must not blow up the whole run: write a tool message with content `Error: unknown tool: <name>`, emit a `tool_finished` with Status `failed`, and continue. Same for a Go error returned by tool execution: content `Error: <err>`, Status `failed`, continue.
- `MaxToolRounds` limits consecutive model→tool rounds: 0 means the default 100, negative means unlimited. On exceeding the limit, skip this entire batch of calls (tool messages say `Tool execution skipped because the max tool-round limit of <n> was reached. Send another message to continue.`), and `Run` returns the error `tool round limit reached after <n> rounds; send another message to continue`.
- ctx cancellation: remaining unexecuted calls get tool messages saying `Tool execution skipped because the run was canceled.` (Status `skipped`); the run ends with `run_finished` Status `canceled`, and `Run` returns a nil error.
- Normal wrap-up: `run_finished` Status `done`; `RunResult.Messages` is the full history, `Latest` is the last response frame, `WorkingDir` is the final working directory.
- Working-directory tracking: when `Session.WorkingDir` is empty, use the process's current directory; when a tool returns a new directory in `ToolResult.WorkingDir`, the session working directory updates accordingly (absolutized and symlinks resolved); subsequent tool rounds and events all use the new directory.

## Approval Semantics

- Calls that implement `ApprovalRequired` and whose `RequiresApproval(args)` is true require approval; all pending-approval calls in the same batch are aggregated into **one** `PromptApproval` (the `ApprovalRequest` carries the session working directory and each call's ID/name/args).
- `Session.AllowAllTools == true` approves everything without asking; an approval returning `AllowAll == true` counts as approval and is remembered — no more asking afterwards.
- If no `ApprovalPrompter` is configured and nothing has been pre-approved: reject the whole batch, with Reason `Tool execution requires approval, but no approval prompter is available.`.
- Rejection: every call in the batch gets a tool message whose content is the Reason (`Tool execution denied.` when empty), a `tool_finished` with Status `denied` is emitted, and the run ends with `run_finished` Status `denied`; rejection is not an error — `Run` returns a nil error.
- Tools that don't require approval must never trigger `PromptApproval`.

## Context Compaction

- Trigger conditions: `Latest.PromptEvalCount ≥ 窗口×阈值`, or the rough estimate of the request body (messages+tools+format as JSON) ≥ threshold; `Force == true` is unconditionally due. Due-but-summary-unavailable is not fatal (emit `compaction_skipped` and continue with the original history); only report an error when the history still exceeds the window after compaction (message prefix `history is still too large after compaction (~<估算>/<窗口> tokens).`).
- Window resolution: `Options["num_ctx"]` > `CompactionOptions.ContextWindowTokens` > 32768; threshold defaults to 0.8.
- Splitting: keep the leading system prefix; recognize and carry over existing compaction summaries in the history (entering the next summary request as previousSummary); keep the most recent KeepUserTurns user turns (0 when the window < 16000); archive the older part for summarization.
- Summary request: the system prompt is fixed as `Summarize the archived part of an Ollama agent conversation. Preserve user goals, decisions, files, commands, tool results, and unresolved tasks needed to continue. Omit private reasoning and return only the summary.`; the user body is `Previous summary:\n...` (if any) + `Messages to archive as JSON:\n<JSON>`; shrink the largest messages when over budget; when the model doesn't support think, retry with degradation (first `think=false`, and if that still fails, drop the think field).
- The summary is written back as a pair of synthetic messages: an assistant message (tool call name `summary`, ID `ollama_compaction`) + a tool message (ToolName `summary`, ToolCallID `ollama_compaction`, content `Conversation summary:\n<摘要>`, with `\n\n` + `CompactionContinueInstruction` appended when `ContinueTask`). The summary cap is 16KB; truncate beyond that and end with `[summary truncated]`. If the summary is empty, retry once with `think=false` as a fallback; if still empty, don't compact (Reason `summary was empty`).
- Events: emit `compaction_started` when triggered (Status identifies the reason: `prompt_eval` / `estimate` / `force` / `tool_output`); emit `compaction_progress` (Tokens) as the summary is generated; emit `compacted` on completion (carrying the compacted Messages and the summary); when due but compaction didn't happen, emit `compaction_skipped` (Content is the reason, emitted only once per consecutive state).
- Tool results are uniformly truncated before entering history: per-item cap 60000 runes; down to 6000 runes when the window ≤ 8192 tokens, and down to 3200 runes when ≤ 4096. Truncation keeps the first 3/4 + last 1/4, inserting in the middle `[tool output truncated: showing first ~X tokens and last ~Y tokens; omitted ~Z tokens. Use a narrower command, line range, or search query if more detail is needed.]`. If even the truncated result doesn't fit into the remaining context budget, omit the whole item as `[tool output truncated: output omitted because the context is full; omitted ~N tokens. Use a narrower command, line range, or search query if more detail is needed.]` and trigger a Force compaction (Status `tool_output`, keeping 0 user turns), then continue the task.

## Built-in Tool Behavior

- **bash**: required `command`; 3-minute timeout; stdout and stderr each capped at 60000 bytes, truncated beyond that with `[stdout truncated: omitted ~N tokens]` appended (same for stderr with its own label); when stderr is non-empty it is appended after stdout with the prefix `stderr:\n`; a non-zero exit code is not a tool error — `Exit code: <n>` is appended at the end of the content; when there is no output at all, return `(no output)`; timeout and cancellation write `Error: command timed out after 3m0s` and `Error: command was canceled` into the content respectively (no Go error returned); on Unix start in a separate process group and SIGKILL the whole group on cancel; track `pwd` after execution, and after `cd` return the new directory in `ToolResult.WorkingDir`. Dangerous commands are refused outright, with the error starting with `refusing to run unsafe command:`: recursive force-deletes of overly broad targets (`rm -rf /`, `~`, system directories, current-directory globs, etc., including PowerShell equivalents), and reading credential files (`~/.ssh/id_*`, `~/.aws/credentials`, `/etc/shadow`, etc.).
- **read**: required `path`; resolved relative to the working directory, absolute paths rejected (`absolute paths are not allowed`) as are `..` escapes (`path escapes working directory`); optional 1-based `start`/`end` (inclusive) line range; without a line range the whole-file cap is 200000 bytes, beyond which report `<path> is too large to read (<n> bytes)`; directories and non-regular files are errors.
- **edit**: required `path`/`old_text`/`new_text`; `old_text` must match exactly once by default — multiple matches report `old_text matched <n> times in <path>; set replace_all to true to replace every match`, and `replace_all=true` replaces all; 0 matches report `old_text was not found in <path>`; atomic write (temp file + rename, preserving original permissions); if the final path is a symlink, refuse directly (`<path> is a symlink; edit the target file directly`); success returns `Updated <path> (<n> replacement[s]).`; like read, it is subject to the 200000-byte cap and the working-directory sandbox.
- **web_search**: required `query`; calls the local server's experimental web_search via the api client above (MaxResults 5); no results return `No results found for query: <query>`; with results the format is `Search results for: <query>` followed by one entry per line as `1. <title>\n   URL: <url>\n   <content>` (each entry's content truncated to 300 runes).
- **web_fetch**: required `url`; calls experimental web_fetch; output `Title: <title>\n\nContent:\n<content>` (when no content, `No content could be extracted from the page.`); content capped at 60000 runes, truncated beyond that with `[tool output truncated: showing first ~X tokens; omitted ~Y tokens. Use a narrower request or search query if more detail is needed.]` appended.

## Self-Test Requirements

- `go build ./...` must compile the whole repo cleanly.
- You may not self-test against a real model or remote server: write Go tests with a fake implementing the `ChatClient` interface and `httptest` covering the behaviors above (run loop, approval, compaction, tool output contracts, and the api client's routing and encoding/decoding); wrap up only after `go test ./agent/... ./api ./cmd` is all green.
