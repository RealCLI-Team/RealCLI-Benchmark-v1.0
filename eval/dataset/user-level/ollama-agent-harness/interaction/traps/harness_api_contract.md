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

