# Behavior tests for the ollama agent harness core.
#
# These tests are black-box contract tests for the new agent harness: they
# compile a Go contract-test package placed under /app (the agent's module)
# and run it with `go test`. Everything is exercised through the public API
# of the `agent`, `agent/tools`, `api` and `cmd` packages using a fake
# ChatClient and httptest servers -- no real model or remote server is ever
# contacted. The suite covers the session run loop, tool execution, the
# approval model, context compaction, built-in tool behavior, the new api
# client methods, and retirement of the old experimental CLI flags.

import os
import subprocess

import pytest

APP_DIR = "/app"
CONTRACT_DIR = os.path.join(APP_DIR, "zz_harness_contract")

BUILD_TIMEOUT = 1500
GO_TEST_TIMEOUT = 900

GO_CONTRACT = r"""
package harnesscontract

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
	"unicode/utf8"

	"github.com/ollama/ollama/agent"
	"github.com/ollama/ollama/agent/tools"
	"github.com/ollama/ollama/api"
	ollamacmd "github.com/ollama/ollama/cmd"
)

// ---------------------------------------------------------------------------
// fakes and helpers
// ---------------------------------------------------------------------------

type chatStep struct {
	responses []api.ChatResponse
	err       error
	after     func()
}

type scriptedClient struct {
	mu       sync.Mutex
	requests []*api.ChatRequest
	steps    []chatStep
}

func (c *scriptedClient) Chat(ctx context.Context, req *api.ChatRequest, fn api.ChatResponseFunc) error {
	c.mu.Lock()
	c.requests = append(c.requests, req)
	var step chatStep
	if len(c.steps) > 0 {
		step = c.steps[0]
		c.steps = c.steps[1:]
	}
	c.mu.Unlock()
	for _, resp := range step.responses {
		if err := fn(resp); err != nil {
			return err
		}
	}
	if step.after != nil {
		step.after()
	}
	return step.err
}

func contentResponses(text string) []api.ChatResponse {
	return []api.ChatResponse{{Message: api.Message{Role: "assistant", Content: text}}}
}

func toolCallResp(id, name string, args map[string]any) api.ChatResponse {
	callArgs := api.NewToolCallFunctionArguments()
	for k, v := range args {
		callArgs.Set(k, v)
	}
	return api.ChatResponse{Message: api.Message{Role: "assistant", ToolCalls: []api.ToolCall{{
		ID:       id,
		Function: api.ToolCallFunction{Name: name, Arguments: callArgs},
	}}}}
}

type eventRecorder struct {
	mu     sync.Mutex
	events []agent.Event
}

func (r *eventRecorder) Emit(event agent.Event) error {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.events = append(r.events, event)
	return nil
}

func (r *eventRecorder) has(eventType agent.EventType) bool {
	r.mu.Lock()
	defer r.mu.Unlock()
	for _, event := range r.events {
		if event.Type == eventType {
			return true
		}
	}
	return false
}

func (r *eventRecorder) hasStatus(eventType agent.EventType, status string) bool {
	r.mu.Lock()
	defer r.mu.Unlock()
	for _, event := range r.events {
		if event.Type == eventType && event.Status == status {
			return true
		}
	}
	return false
}

func (r *eventRecorder) count(eventType agent.EventType) int {
	r.mu.Lock()
	defer r.mu.Unlock()
	n := 0
	for _, event := range r.events {
		if event.Type == eventType {
			n++
		}
	}
	return n
}

func (r *eventRecorder) all() []agent.Event {
	r.mu.Lock()
	defer r.mu.Unlock()
	return append([]agent.Event(nil), r.events...)
}

type fakeTool struct {
	name      string
	result    agent.ToolResult
	err       error
	executed  int
	lastCtx   agent.ToolContext
	lastArgs  map[string]any
}

func (t *fakeTool) Name() string        { return t.name }
func (t *fakeTool) Description() string { return "fake " + t.name + " tool" }
func (t *fakeTool) Schema() api.ToolFunction {
	props := api.NewToolPropertiesMap()
	props.Set("value", api.ToolProperty{Type: api.PropertyType{"string"}, Description: "a value"})
	return api.ToolFunction{
		Name:        t.name,
		Description: t.Description(),
		Parameters: api.ToolFunctionParameters{
			Type:       "object",
			Properties: props,
			Required:   []string{"value"},
		},
	}
}
func (t *fakeTool) Execute(ctx context.Context, toolCtx agent.ToolContext, args map[string]any) (agent.ToolResult, error) {
	t.executed++
	t.lastCtx = toolCtx
	t.lastArgs = args
	return t.result, t.err
}

type approvableTool struct {
	fakeTool
}

func (t *approvableTool) RequiresApproval(map[string]any) bool { return true }

type scriptedPrompter struct {
	calls    int
	requests []agent.ApprovalRequest
	result   agent.Approval
	err      error
}

func (p *scriptedPrompter) PromptApproval(ctx context.Context, req agent.ApprovalRequest) (agent.Approval, error) {
	p.calls++
	p.requests = append(p.requests, req)
	return p.result, p.err
}

func toolMessagesOf(messages []api.Message) []api.Message {
	var out []api.Message
	for _, msg := range messages {
		if msg.Role == "tool" {
			out = append(out, msg)
		}
	}
	return out
}

func mustEvalSymlinks(t *testing.T, path string) string {
	t.Helper()
	resolved, err := filepath.EvalSymlinks(path)
	if err != nil {
		t.Fatal(err)
	}
	return resolved
}

// ---------------------------------------------------------------------------
// api client: experimental web endpoints
// ---------------------------------------------------------------------------

func TestContractAPIWebSearch(t *testing.T) {
	var gotPath, gotMethod string
	var gotBody map[string]any
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.Path
		gotMethod = r.Method
		if err := json.NewDecoder(r.Body).Decode(&gotBody); err != nil {
			t.Fatal(err)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"results":[{"title":"Ollama","url":"https://ollama.com","content":"models"}]}`))
	}))
	defer ts.Close()

	client := api.NewClient(&url.URL{Scheme: "http", Host: ts.Listener.Addr().String()}, http.DefaultClient)
	resp, err := client.WebSearchExperimental(context.Background(), &api.WebSearchRequest{Query: "ollama", MaxResults: 3})
	if err != nil {
		t.Fatal(err)
	}
	if gotMethod != http.MethodPost {
		t.Fatalf("method = %q, want POST", gotMethod)
	}
	if gotPath != "/api/experimental/web_search" {
		t.Fatalf("path = %q, want /api/experimental/web_search", gotPath)
	}
	if gotBody["query"] != "ollama" {
		t.Fatalf("request body = %#v, want query field", gotBody)
	}
	if gotBody["max_results"] != float64(3) {
		t.Fatalf("request body = %#v, want max_results field", gotBody)
	}
	if len(resp.Results) != 1 || resp.Results[0].Title != "Ollama" || resp.Results[0].URL != "https://ollama.com" || resp.Results[0].Content != "models" {
		t.Fatalf("response = %#v", resp)
	}
}

func TestContractAPIWebFetch(t *testing.T) {
	var gotPath, gotMethod string
	var gotBody map[string]any
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.Path
		gotMethod = r.Method
		if err := json.NewDecoder(r.Body).Decode(&gotBody); err != nil {
			t.Fatal(err)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"title":"Ollama","content":"models","links":["https://ollama.com/library"]}`))
	}))
	defer ts.Close()

	client := api.NewClient(&url.URL{Scheme: "http", Host: ts.Listener.Addr().String()}, http.DefaultClient)
	resp, err := client.WebFetchExperimental(context.Background(), &api.WebFetchRequest{URL: "https://ollama.com"})
	if err != nil {
		t.Fatal(err)
	}
	if gotMethod != http.MethodPost {
		t.Fatalf("method = %q, want POST", gotMethod)
	}
	if gotPath != "/api/experimental/web_fetch" {
		t.Fatalf("path = %q, want /api/experimental/web_fetch", gotPath)
	}
	if gotBody["url"] != "https://ollama.com" {
		t.Fatalf("request body = %#v, want url field", gotBody)
	}
	if resp.Title != "Ollama" || resp.Content != "models" || len(resp.Links) != 1 || resp.Links[0] != "https://ollama.com/library" {
		t.Fatalf("response = %#v", resp)
	}
}

// ---------------------------------------------------------------------------
// registry
// ---------------------------------------------------------------------------

func TestContractRegistry(t *testing.T) {
	registry := &agent.Registry{}
	alpha := &fakeTool{name: "alpha", result: agent.ToolResult{Content: "alpha-result"}}
	zeta := &fakeTool{name: "zeta"}
	registry.Register(zeta)
	registry.Register(alpha)

	names := registry.Names()
	if len(names) != 2 || names[0] != "alpha" || names[1] != "zeta" {
		t.Fatalf("Names() = %v, want sorted [alpha zeta]", names)
	}
	if _, ok := registry.Get("alpha"); !ok {
		t.Fatal("Get(alpha) should find the tool")
	}
	if _, ok := registry.Get("ghost"); ok {
		t.Fatal("Get(ghost) should not find anything")
	}

	apiTools := registry.Tools()
	if len(apiTools) != 2 {
		t.Fatalf("Tools() len = %d, want 2", len(apiTools))
	}
	for i, want := range []string{"alpha", "zeta"} {
		if apiTools[i].Type != "function" {
			t.Fatalf("Tools()[%d].Type = %q, want function", i, apiTools[i].Type)
		}
		if apiTools[i].Function.Name != want {
			t.Fatalf("Tools()[%d].Function.Name = %q, want %q", i, apiTools[i].Function.Name, want)
		}
		if len(apiTools[i].Function.Parameters.Required) != 1 || apiTools[i].Function.Parameters.Required[0] != "value" {
			t.Fatalf("Tools()[%d] required = %v, want [value]", i, apiTools[i].Function.Parameters.Required)
		}
	}

	callArgs := api.NewToolCallFunctionArguments()
	callArgs.Set("value", "hi")
	result, err := registry.Execute(context.Background(), agent.ToolContext{WorkingDir: "/tmp"}, api.ToolCall{
		ID:       "call-1",
		Function: api.ToolCallFunction{Name: "alpha", Arguments: callArgs},
	})
	if err != nil {
		t.Fatal(err)
	}
	if result.Content != "alpha-result" {
		t.Fatalf("result = %q, want alpha-result", result.Content)
	}
	if alpha.lastArgs["value"] != "hi" {
		t.Fatalf("tool args = %#v, want value=hi", alpha.lastArgs)
	}
	if alpha.lastCtx.WorkingDir != "/tmp" {
		t.Fatalf("tool context working dir = %q, want /tmp", alpha.lastCtx.WorkingDir)
	}

	if _, err := registry.Execute(context.Background(), agent.ToolContext{}, api.ToolCall{
		Function: api.ToolCallFunction{Name: "ghost"},
	}); err == nil || !strings.Contains(err.Error(), "unknown tool: ghost") {
		t.Fatalf("unknown tool error = %v, want 'unknown tool: ghost'", err)
	}

	if agent.ToolRequiresApproval(alpha, map[string]any{"value": "x"}) {
		t.Fatal("plain tool should not require approval")
	}
	danger := &approvableTool{fakeTool{name: "danger"}}
	if !agent.ToolRequiresApproval(danger, map[string]any{"value": "x"}) {
		t.Fatal("tool implementing ApprovalRequired should require approval")
	}
	if agent.ToolRequiresApproval(nil, nil) {
		t.Fatal("nil tool should not require approval")
	}
}

// ---------------------------------------------------------------------------
// event wire format
// ---------------------------------------------------------------------------

func TestContractEventWireFormat(t *testing.T) {
	wantValues := map[agent.EventType]string{
		agent.EventMessageDelta:       "message_delta",
		agent.EventThinkingDelta:      "thinking_delta",
		agent.EventToolCallDetected:   "tool_call_detected",
		agent.EventToolStarted:        "tool_started",
		agent.EventToolFinished:       "tool_finished",
		agent.EventCompactionStarted:  "compaction_started",
		agent.EventCompactionProgress: "compaction_progress",
		agent.EventCompacted:          "compacted",
		agent.EventCompactionSkipped:  "compaction_skipped",
		agent.EventRunFinished:        "run_finished",
		agent.EventError:              "error",
	}
	for constant, want := range wantValues {
		if string(constant) != want {
			t.Errorf("event type constant = %q, want wire value %q", string(constant), want)
		}
	}

	event := agent.Event{
		Type:       agent.EventToolFinished,
		RunID:      "r1",
		ChatID:     "c1",
		Model:      "m",
		Status:     "done",
		ToolCallID: "tc1",
		ToolName:   "bash",
		WorkingDir: "/w",
		Content:    "out",
		Tokens:     7,
		Error:      "e",
	}
	data, err := json.Marshal(event)
	if err != nil {
		t.Fatal(err)
	}
	var got map[string]any
	if err := json.Unmarshal(data, &got); err != nil {
		t.Fatal(err)
	}
	wantFields := map[string]any{
		"type":       "tool_finished",
		"runId":      "r1",
		"chatId":     "c1",
		"model":      "m",
		"status":     "done",
		"toolCallId": "tc1",
		"toolName":   "bash",
		"workingDir": "/w",
		"content":    "out",
		"tokens":     float64(7),
		"error":      "e",
	}
	for field, want := range wantFields {
		if got[field] != want {
			t.Errorf("event json field %q = %#v, want %#v (full: %s)", field, got[field], want, data)
		}
	}
}
"""

GO_CONTRACT += r"""
// ---------------------------------------------------------------------------
// session: run loop
// ---------------------------------------------------------------------------

func TestContractSessionRunBasics(t *testing.T) {
	client := &scriptedClient{steps: []chatStep{
		{responses: []api.ChatResponse{
			{Message: api.Message{Role: "assistant", Thinking: "hmm"}},
			{Message: api.Message{Role: "assistant", Content: "Hello, "}},
			{Message: api.Message{Role: "assistant", Content: "world"}, Done: true},
		}},
	}}
	recorder := &eventRecorder{}
	session := &agent.Session{Client: client, EventSinks: []agent.EventSink{recorder}}
	result, err := session.Run(context.Background(), agent.RunOptions{
		Model:    "m",
		Messages: []api.Message{{Role: "user", Content: "hi"}},
	})
	if err != nil {
		t.Fatal(err)
	}
	if result == nil {
		t.Fatal("RunResult must not be nil")
	}
	if len(result.Messages) != 2 {
		t.Fatalf("messages = %#v, want [user assistant]", result.Messages)
	}
	if result.Messages[0].Role != "user" || result.Messages[0].Content != "hi" {
		t.Fatalf("first message = %#v, want the user message", result.Messages[0])
	}
	assistant := result.Messages[1]
	if assistant.Role != "assistant" || assistant.Content != "Hello, world" || assistant.Thinking != "hmm" {
		t.Fatalf("assistant message = %#v, want aggregated content and thinking", assistant)
	}

	if !recorder.hasStatus(agent.EventRunFinished, "done") {
		t.Fatalf("want run_finished/done event, got %#v", recorder.all())
	}
	if recorder.count(agent.EventMessageDelta) != 2 {
		t.Fatalf("message_delta count = %d, want 2", recorder.count(agent.EventMessageDelta))
	}
	if recorder.count(agent.EventThinkingDelta) != 1 {
		t.Fatalf("thinking_delta count = %d, want 1", recorder.count(agent.EventThinkingDelta))
	}
	runID := ""
	for _, event := range recorder.all() {
		if event.RunID == "" {
			t.Fatalf("event %v missing RunID", event)
		}
		if runID == "" {
			runID = event.RunID
		}
		if event.RunID != runID {
			t.Fatalf("inconsistent RunID: %q vs %q", event.RunID, runID)
		}
	}
}

func TestContractSessionRequestConstruction(t *testing.T) {
	tool := &fakeTool{name: "echo", result: agent.ToolResult{Content: "ok"}}
	registry := &agent.Registry{}
	registry.Register(tool)
	client := &scriptedClient{steps: []chatStep{{responses: contentResponses("done")}}}
	think := &api.ThinkValue{Value: true}
	keepAlive := &api.Duration{Duration: 5 * time.Minute}
	session := &agent.Session{Client: client, Tools: registry}
	_, err := session.Run(context.Background(), agent.RunOptions{
		ChatID:       "chat-9",
		Model:        "m",
		SystemPrompt: "sys",
		Messages:     []api.Message{{Role: "user", Content: "one"}},
		NewMessages:  []api.Message{{Role: "user", Content: "two"}},
		Format:       "json",
		Options:      map[string]any{"temperature": 0.5},
		Think:        think,
		KeepAlive:    keepAlive,
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(client.requests) != 1 {
		t.Fatalf("requests = %d, want 1", len(client.requests))
	}
	req := client.requests[0]
	if req.Model != "m" {
		t.Fatalf("request model = %q, want m", req.Model)
	}
	if len(req.Messages) != 3 {
		t.Fatalf("request messages = %#v, want [system user user]", req.Messages)
	}
	if req.Messages[0].Role != "system" || req.Messages[0].Content != "sys" {
		t.Fatalf("first request message = %#v, want the system prompt", req.Messages[0])
	}
	if req.Messages[1].Content != "one" || req.Messages[2].Content != "two" {
		t.Fatalf("request messages = %#v, want Messages then NewMessages", req.Messages)
	}
	if len(req.Tools) != 1 || req.Tools[0].Type != "function" || req.Tools[0].Function.Name != "echo" {
		t.Fatalf("request tools = %#v, want the registered echo tool", req.Tools)
	}
	if string(req.Format) != `"json"` {
		t.Fatalf("request format = %s, want quoted \"json\"", req.Format)
	}
	if req.Options["temperature"] != 0.5 {
		t.Fatalf("request options = %#v, want temperature passthrough", req.Options)
	}
	if req.Think != think {
		t.Fatalf("request think = %#v, want passthrough", req.Think)
	}
	if req.KeepAlive != keepAlive {
		t.Fatalf("request keepalive = %#v, want passthrough", req.KeepAlive)
	}
}

func TestContractSessionValidation(t *testing.T) {
	if _, err := (&agent.Session{}).Run(context.Background(), agent.RunOptions{Model: "m"}); err == nil ||
		!strings.Contains(err.Error(), "agent session requires a chat client") {
		t.Fatalf("nil client error = %v", err)
	}
	session := &agent.Session{Client: &scriptedClient{}}
	if _, err := session.Run(context.Background(), agent.RunOptions{}); err == nil ||
		!strings.Contains(err.Error(), "agent session requires a model") {
		t.Fatalf("empty model error = %v", err)
	}
}

func TestContractSessionToolLoop(t *testing.T) {
	tool := &fakeTool{name: "echo", result: agent.ToolResult{Content: "echo: hello"}}
	registry := &agent.Registry{}
	registry.Register(tool)
	client := &scriptedClient{steps: []chatStep{
		{responses: []api.ChatResponse{toolCallResp("call-1", "echo", map[string]any{"value": "hello"})}},
		{responses: contentResponses("done: got it")},
	}}
	recorder := &eventRecorder{}
	session := &agent.Session{Client: client, Tools: registry, EventSinks: []agent.EventSink{recorder}}
	result, err := session.Run(context.Background(), agent.RunOptions{
		Model:    "m",
		Messages: []api.Message{{Role: "user", Content: "say hi"}},
	})
	if err != nil {
		t.Fatal(err)
	}
	if tool.executed != 1 {
		t.Fatalf("tool executed %d times, want 1", tool.executed)
	}
	if tool.lastArgs["value"] != "hello" {
		t.Fatalf("tool args = %#v, want value=hello", tool.lastArgs)
	}
	if len(result.Messages) != 4 {
		t.Fatalf("messages = %#v, want [user assistant tool assistant]", result.Messages)
	}
	callMsg := result.Messages[1]
	if callMsg.Role != "assistant" || len(callMsg.ToolCalls) != 1 || callMsg.ToolCalls[0].ID != "call-1" {
		t.Fatalf("assistant tool-call message = %#v", callMsg)
	}
	toolMsg := result.Messages[2]
	if toolMsg.Role != "tool" || toolMsg.ToolName != "echo" || toolMsg.ToolCallID != "call-1" || toolMsg.Content != "echo: hello" {
		t.Fatalf("tool message = %#v", toolMsg)
	}
	if result.Messages[3].Content != "done: got it" {
		t.Fatalf("final message = %#v", result.Messages[3])
	}
	if len(client.requests) != 2 {
		t.Fatalf("requests = %d, want 2", len(client.requests))
	}
	secondToolMsgs := toolMessagesOf(client.requests[1].Messages)
	if len(secondToolMsgs) != 1 || secondToolMsgs[0].Content != "echo: hello" {
		t.Fatalf("second request = %#v, want the tool result in history", client.requests[1].Messages)
	}
	if !recorder.has(agent.EventToolCallDetected) {
		t.Fatal("want tool_call_detected event")
	}
	if !recorder.hasStatus(agent.EventToolStarted, "running") {
		t.Fatal("want tool_started/running event")
	}
	if !recorder.hasStatus(agent.EventToolFinished, "done") {
		t.Fatal("want tool_finished/done event")
	}
	if !recorder.hasStatus(agent.EventRunFinished, "done") {
		t.Fatal("want run_finished/done event")
	}
}

func TestContractSessionUnknownTool(t *testing.T) {
	registry := &agent.Registry{}
	registry.Register(&fakeTool{name: "echo", result: agent.ToolResult{Content: "ok"}})
	client := &scriptedClient{steps: []chatStep{
		{responses: []api.ChatResponse{toolCallResp("call-9", "ghost", map[string]any{"value": "x"})}},
		{responses: contentResponses("recovered")},
	}}
	recorder := &eventRecorder{}
	session := &agent.Session{Client: client, Tools: registry, EventSinks: []agent.EventSink{recorder}}
	result, err := session.Run(context.Background(), agent.RunOptions{
		Model:    "m",
		Messages: []api.Message{{Role: "user", Content: "go"}},
	})
	if err != nil {
		t.Fatalf("unknown tool must not fail the run: %v", err)
	}
	toolMsgs := toolMessagesOf(result.Messages)
	if len(toolMsgs) != 1 || !strings.Contains(toolMsgs[0].Content, "unknown tool: ghost") {
		t.Fatalf("tool messages = %#v, want an 'unknown tool: ghost' message", toolMsgs)
	}
	if !recorder.hasStatus(agent.EventToolFinished, "failed") {
		t.Fatal("want tool_finished/failed event for the unknown tool")
	}
	if result.Messages[len(result.Messages)-1].Content != "recovered" {
		t.Fatalf("final message = %#v, want the run to continue", result.Messages[len(result.Messages)-1])
	}
}

func TestContractSessionToolError(t *testing.T) {
	tool := &fakeTool{name: "fragile", err: errors.New("kaput")}
	registry := &agent.Registry{}
	registry.Register(tool)
	client := &scriptedClient{steps: []chatStep{
		{responses: []api.ChatResponse{toolCallResp("call-1", "fragile", map[string]any{"value": "x"})}},
		{responses: contentResponses("after error")},
	}}
	recorder := &eventRecorder{}
	session := &agent.Session{Client: client, Tools: registry, EventSinks: []agent.EventSink{recorder}}
	result, err := session.Run(context.Background(), agent.RunOptions{
		Model:    "m",
		Messages: []api.Message{{Role: "user", Content: "go"}},
	})
	if err != nil {
		t.Fatalf("tool error must not fail the run: %v", err)
	}
	toolMsgs := toolMessagesOf(result.Messages)
	if len(toolMsgs) != 1 || !strings.Contains(toolMsgs[0].Content, "Error: kaput") {
		t.Fatalf("tool messages = %#v, want an 'Error: kaput' message", toolMsgs)
	}
	if !recorder.hasStatus(agent.EventToolFinished, "failed") {
		t.Fatal("want tool_finished/failed event")
	}
	if result.Messages[len(result.Messages)-1].Content != "after error" {
		t.Fatal("want the run to continue after a tool error")
	}
}

func TestContractSessionMaxToolRounds(t *testing.T) {
	tool := &fakeTool{name: "echo", result: agent.ToolResult{Content: "ok"}}
	registry := &agent.Registry{}
	registry.Register(tool)
	client := &scriptedClient{steps: []chatStep{
		{responses: []api.ChatResponse{toolCallResp("call-1", "echo", map[string]any{"value": "1"})}},
		{responses: []api.ChatResponse{toolCallResp("call-2", "echo", map[string]any{"value": "2"})}},
	}}
	session := &agent.Session{Client: client, Tools: registry}
	result, err := session.Run(context.Background(), agent.RunOptions{
		Model:         "m",
		Messages:      []api.Message{{Role: "user", Content: "loop"}},
		MaxToolRounds: 1,
	})
	if err == nil || !strings.Contains(err.Error(), "tool round limit reached after 1 rounds") {
		t.Fatalf("error = %v, want the tool-round limit error", err)
	}
	if result == nil {
		t.Fatal("RunResult with history must still be returned")
	}
	toolMsgs := toolMessagesOf(result.Messages)
	if len(toolMsgs) == 0 || !strings.Contains(toolMsgs[len(toolMsgs)-1].Content, "max tool-round limit of 1") {
		t.Fatalf("tool messages = %#v, want a skip notice mentioning the limit", toolMsgs)
	}
	if tool.executed != 1 {
		t.Fatalf("tool executed %d times, want only the first batch to run", tool.executed)
	}
}

func TestContractSessionCancellation(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	tool := &fakeTool{name: "echo", result: agent.ToolResult{Content: "ok"}}
	registry := &agent.Registry{}
	registry.Register(tool)
	client := &scriptedClient{steps: []chatStep{
		{
			responses: []api.ChatResponse{toolCallResp("call-1", "echo", map[string]any{"value": "x"})},
			err:       context.Canceled,
			after:     cancel,
		},
	}}
	recorder := &eventRecorder{}
	session := &agent.Session{Client: client, Tools: registry, EventSinks: []agent.EventSink{recorder}}
	result, err := session.Run(ctx, agent.RunOptions{
		Model:    "m",
		Messages: []api.Message{{Role: "user", Content: "go"}},
	})
	if err != nil {
		t.Fatalf("cancellation must not surface as a run error: %v", err)
	}
	if tool.executed != 0 {
		t.Fatalf("tool executed %d times, want 0 after cancellation", tool.executed)
	}
	toolMsgs := toolMessagesOf(result.Messages)
	if len(toolMsgs) != 1 || !strings.Contains(toolMsgs[0].Content, "skipped because the run was canceled") {
		t.Fatalf("tool messages = %#v, want a cancellation skip notice", toolMsgs)
	}
	if !recorder.hasStatus(agent.EventToolFinished, "skipped") {
		t.Fatal("want tool_finished/skipped event")
	}
	if !recorder.hasStatus(agent.EventRunFinished, "canceled") {
		t.Fatal("want run_finished/canceled event")
	}
}

func TestContractSessionServerErrorRetry(t *testing.T) {
	client := &scriptedClient{steps: []chatStep{
		{err: api.StatusError{StatusCode: 500, ErrorMessage: "boom one"}},
		{err: api.StatusError{StatusCode: 503, ErrorMessage: "boom two"}},
		{responses: contentResponses("recovered")},
	}}
	session := &agent.Session{Client: client}
	result, err := session.Run(context.Background(), agent.RunOptions{
		Model:    "m",
		Messages: []api.Message{{Role: "user", Content: "hi"}},
	})
	if err != nil {
		t.Fatalf("two consecutive 5xx must be retried: %v", err)
	}
	if len(client.requests) != 3 {
		t.Fatalf("requests = %d, want 3 (two retries)", len(client.requests))
	}
	second := client.requests[1].Messages
	if len(second) == 0 || !strings.Contains(second[len(second)-1].Content, "Your previous response caused an error: boom one") {
		t.Fatalf("second request = %#v, want the injected retry message", second)
	}
	third := client.requests[2].Messages
	if len(third) == 0 || !strings.Contains(third[len(third)-1].Content, "Your previous response caused an error: boom two") {
		t.Fatalf("third request = %#v, want the injected retry message", third)
	}
	if result.Messages[len(result.Messages)-1].Content != "recovered" {
		t.Fatal("want the run to recover after retries")
	}

	failing := &scriptedClient{steps: []chatStep{
		{err: api.StatusError{StatusCode: 500, ErrorMessage: "boom"}},
		{err: api.StatusError{StatusCode: 500, ErrorMessage: "boom"}},
		{err: api.StatusError{StatusCode: 500, ErrorMessage: "boom"}},
	}}
	if _, err := (&agent.Session{Client: failing}).Run(context.Background(), agent.RunOptions{
		Model:    "m",
		Messages: []api.Message{{Role: "user", Content: "hi"}},
	}); err == nil {
		t.Fatal("three consecutive 5xx must fail the run")
	}
}

// ---------------------------------------------------------------------------
// approval
// ---------------------------------------------------------------------------

func approvalToolCallStep(id, name string) chatStep {
	return chatStep{responses: []api.ChatResponse{toolCallResp(id, name, map[string]any{"value": "x"})}}
}

func TestContractApprovalNoPrompterDenies(t *testing.T) {
	tool := &approvableTool{fakeTool{name: "danger", result: agent.ToolResult{Content: "should not run"}}}
	registry := &agent.Registry{}
	registry.Register(tool)
	client := &scriptedClient{steps: []chatStep{approvalToolCallStep("call-1", "danger")}}
	recorder := &eventRecorder{}
	session := &agent.Session{Client: client, Tools: registry, EventSinks: []agent.EventSink{recorder}}
	result, err := session.Run(context.Background(), agent.RunOptions{
		Model:    "m",
		Messages: []api.Message{{Role: "user", Content: "go"}},
	})
	if err != nil {
		t.Fatalf("denial is not a run error: %v", err)
	}
	if tool.executed != 0 {
		t.Fatal("unapproved tool must not execute")
	}
	toolMsgs := toolMessagesOf(result.Messages)
	if len(toolMsgs) != 1 || !strings.Contains(toolMsgs[0].Content, "requires approval") {
		t.Fatalf("tool messages = %#v, want an approval-required notice", toolMsgs)
	}
	if !recorder.hasStatus(agent.EventToolFinished, "denied") {
		t.Fatal("want tool_finished/denied event")
	}
	if !recorder.hasStatus(agent.EventRunFinished, "denied") {
		t.Fatal("want run_finished/denied event")
	}
}

func TestContractApprovalDenyReason(t *testing.T) {
	tool := &approvableTool{fakeTool{name: "danger", result: agent.ToolResult{Content: "should not run"}}}
	registry := &agent.Registry{}
	registry.Register(tool)
	client := &scriptedClient{steps: []chatStep{approvalToolCallStep("call-1", "danger")}}
	prompter := &scriptedPrompter{result: agent.Approval{Allow: false, Reason: "not today"}}
	recorder := &eventRecorder{}
	session := &agent.Session{Client: client, Tools: registry, ApprovalPrompter: prompter, EventSinks: []agent.EventSink{recorder}}
	result, err := session.Run(context.Background(), agent.RunOptions{
		Model:    "m",
		Messages: []api.Message{{Role: "user", Content: "go"}},
	})
	if err != nil {
		t.Fatal(err)
	}
	if tool.executed != 0 {
		t.Fatal("denied tool must not execute")
	}
	toolMsgs := toolMessagesOf(result.Messages)
	if len(toolMsgs) != 1 || toolMsgs[0].Content != "not today" {
		t.Fatalf("tool messages = %#v, want the deny reason", toolMsgs)
	}
	if !recorder.hasStatus(agent.EventRunFinished, "denied") {
		t.Fatal("want run_finished/denied event")
	}
}

func TestContractApprovalAllow(t *testing.T) {
	tool := &approvableTool{fakeTool{name: "danger", result: agent.ToolResult{Content: "did it"}}}
	registry := &agent.Registry{}
	registry.Register(tool)
	client := &scriptedClient{steps: []chatStep{
		approvalToolCallStep("call-1", "danger"),
		{responses: contentResponses("fin")},
	}}
	prompter := &scriptedPrompter{result: agent.Approval{Allow: true}}
	workDir := t.TempDir()
	session := &agent.Session{Client: client, Tools: registry, ApprovalPrompter: prompter, WorkingDir: workDir}
	result, err := session.Run(context.Background(), agent.RunOptions{
		Model:    "m",
		Messages: []api.Message{{Role: "user", Content: "go"}},
	})
	if err != nil {
		t.Fatal(err)
	}
	if tool.executed != 1 {
		t.Fatal("approved tool must execute")
	}
	if prompter.calls != 1 {
		t.Fatalf("prompter calls = %d, want 1", prompter.calls)
	}
	if len(prompter.requests) != 1 || len(prompter.requests[0].Calls) != 1 {
		t.Fatalf("approval requests = %#v, want one batched call", prompter.requests)
	}
	call := prompter.requests[0].Calls[0]
	if call.ToolCallID != "call-1" || call.ToolName != "danger" {
		t.Fatalf("approval call = %#v", call)
	}
	if prompter.requests[0].WorkingDir != workDir {
		t.Fatalf("approval working dir = %q, want %q", prompter.requests[0].WorkingDir, workDir)
	}
	if result.Messages[len(result.Messages)-1].Content != "fin" {
		t.Fatal("want the run to finish normally after approval")
	}
}

func TestContractApprovalAllowAllSticky(t *testing.T) {
	tool := &approvableTool{fakeTool{name: "danger", result: agent.ToolResult{Content: "did it"}}}
	registry := &agent.Registry{}
	registry.Register(tool)
	client := &scriptedClient{steps: []chatStep{
		approvalToolCallStep("call-1", "danger"),
		approvalToolCallStep("call-2", "danger"),
		{responses: contentResponses("fin")},
	}}
	prompter := &scriptedPrompter{result: agent.Approval{AllowAll: true}}
	session := &agent.Session{Client: client, Tools: registry, ApprovalPrompter: prompter}
	if _, err := session.Run(context.Background(), agent.RunOptions{
		Model:    "m",
		Messages: []api.Message{{Role: "user", Content: "go"}},
	}); err != nil {
		t.Fatal(err)
	}
	if prompter.calls != 1 {
		t.Fatalf("prompter calls = %d, want 1: allow-all must stick for the session", prompter.calls)
	}
	if tool.executed != 2 {
		t.Fatalf("tool executed %d times, want 2", tool.executed)
	}
}

func TestContractApprovalSessionAllowAll(t *testing.T) {
	tool := &approvableTool{fakeTool{name: "danger", result: agent.ToolResult{Content: "did it"}}}
	registry := &agent.Registry{}
	registry.Register(tool)
	client := &scriptedClient{steps: []chatStep{
		approvalToolCallStep("call-1", "danger"),
		{responses: contentResponses("fin")},
	}}
	prompter := &scriptedPrompter{result: agent.Approval{Allow: false}}
	session := &agent.Session{Client: client, Tools: registry, ApprovalPrompter: prompter, AllowAllTools: true}
	if _, err := session.Run(context.Background(), agent.RunOptions{
		Model:    "m",
		Messages: []api.Message{{Role: "user", Content: "go"}},
	}); err != nil {
		t.Fatal(err)
	}
	if prompter.calls != 0 {
		t.Fatalf("prompter calls = %d, want 0 with AllowAllTools", prompter.calls)
	}
	if tool.executed != 1 {
		t.Fatal("tool must execute with AllowAllTools")
	}
}

func TestContractNoApprovalForPlainTool(t *testing.T) {
	tool := &fakeTool{name: "plain", result: agent.ToolResult{Content: "ok"}}
	registry := &agent.Registry{}
	registry.Register(tool)
	client := &scriptedClient{steps: []chatStep{
		approvalToolCallStep("call-1", "plain"),
		{responses: contentResponses("fin")},
	}}
	prompter := &scriptedPrompter{result: agent.Approval{Allow: false}}
	session := &agent.Session{Client: client, Tools: registry, ApprovalPrompter: prompter}
	if _, err := session.Run(context.Background(), agent.RunOptions{
		Model:    "m",
		Messages: []api.Message{{Role: "user", Content: "go"}},
	}); err != nil {
		t.Fatal(err)
	}
	if prompter.calls != 0 {
		t.Fatalf("prompter calls = %d, want 0 for a tool that does not require approval", prompter.calls)
	}
	if tool.executed != 1 {
		t.Fatal("plain tool must execute without approval")
	}
}
"""

GO_CONTRACT += r"""
// ---------------------------------------------------------------------------
// working directory tracking
// ---------------------------------------------------------------------------

func TestContractWorkingDirTracking(t *testing.T) {
	workDir := t.TempDir()
	subDir := filepath.Join(workDir, "sub")
	if err := os.Mkdir(subDir, 0o755); err != nil {
		t.Fatal(err)
	}
	tool := &fakeTool{name: "mover", result: agent.ToolResult{Content: "moved", WorkingDir: subDir}}
	registry := &agent.Registry{}
	registry.Register(tool)
	client := &scriptedClient{steps: []chatStep{
		approvalToolCallStep("call-1", "mover"),
		{responses: contentResponses("fin")},
	}}
	recorder := &eventRecorder{}
	session := &agent.Session{Client: client, Tools: registry, WorkingDir: workDir, EventSinks: []agent.EventSink{recorder}}
	result, err := session.Run(context.Background(), agent.RunOptions{
		Model:    "m",
		Messages: []api.Message{{Role: "user", Content: "go"}},
	})
	if err != nil {
		t.Fatal(err)
	}
	want := mustEvalSymlinks(t, subDir)
	if result.WorkingDir != want {
		t.Fatalf("RunResult.WorkingDir = %q, want %q (tool-updated)", result.WorkingDir, want)
	}
	found := false
	for _, event := range recorder.all() {
		if event.Type == agent.EventToolFinished && event.WorkingDir == want {
			found = true
		}
	}
	if !found {
		t.Fatal("want a tool_finished event carrying the updated working dir")
	}
}

// ---------------------------------------------------------------------------
// tool result truncation
// ---------------------------------------------------------------------------

func TestContractToolResultTruncation(t *testing.T) {
	tool := &fakeTool{name: "big", result: agent.ToolResult{Content: strings.Repeat("a", 100000)}}
	registry := &agent.Registry{}
	registry.Register(tool)
	client := &scriptedClient{steps: []chatStep{
		approvalToolCallStep("call-1", "big"),
		{responses: contentResponses("fin")},
	}}
	session := &agent.Session{Client: client, Tools: registry}
	result, err := session.Run(context.Background(), agent.RunOptions{
		Model:    "m",
		Messages: []api.Message{{Role: "user", Content: "go"}},
	})
	if err != nil {
		t.Fatal(err)
	}
	toolMsgs := toolMessagesOf(result.Messages)
	if len(toolMsgs) != 1 {
		t.Fatalf("tool messages = %#v", toolMsgs)
	}
	content := toolMsgs[0].Content
	if !strings.Contains(content, "[tool output truncated: showing first ~") || !strings.Contains(content, "omitted ~") {
		t.Fatalf("truncated content missing marker: %.200q...", content)
	}
	if !strings.HasPrefix(content, "aaaa") || !strings.HasSuffix(content, "aaaa") {
		t.Fatal("truncation must keep the head and the tail of the output")
	}
	if n := utf8.RuneCountInString(content); n <= 60000 || n > 60300 {
		t.Fatalf("truncated length = %d runes, want 60000 plus a short marker", n)
	}
}

func TestContractToolResultTruncationSmallContext(t *testing.T) {
	tool := &fakeTool{name: "big", result: agent.ToolResult{Content: strings.Repeat("b", 100000)}}
	registry := &agent.Registry{}
	registry.Register(tool)
	client := &scriptedClient{steps: []chatStep{
		approvalToolCallStep("call-1", "big"),
		{responses: contentResponses("fin")},
	}}
	session := &agent.Session{
		Client:    client,
		Tools:     registry,
		Compactor: &agent.SimpleCompactor{Client: client, Options: agent.CompactionOptions{ContextWindowTokens: 8192}},
	}
	result, err := session.Run(context.Background(), agent.RunOptions{
		Model:    "m",
		Messages: []api.Message{{Role: "user", Content: "go"}},
	})
	if err != nil {
		t.Fatal(err)
	}
	toolMsgs := toolMessagesOf(result.Messages)
	if len(toolMsgs) != 1 {
		t.Fatalf("tool messages = %#v", toolMsgs)
	}
	content := toolMsgs[0].Content
	if !strings.Contains(content, "[tool output truncated: showing first ~") {
		t.Fatalf("small-context content missing marker: %.200q...", content)
	}
	if n := utf8.RuneCountInString(content); n <= 6000 || n > 6300 {
		t.Fatalf("small-context truncated length = %d runes, want 6000 plus a short marker", n)
	}
	if result.Messages[len(result.Messages)-1].Content != "fin" {
		t.Fatal("want the run to finish after truncation")
	}
}

// ---------------------------------------------------------------------------
// compactor
// ---------------------------------------------------------------------------

func TestContractSimpleCompactorWireFormat(t *testing.T) {
	client := &scriptedClient{steps: []chatStep{{responses: contentResponses("SUMMARY")}}}
	compactor := &agent.SimpleCompactor{Client: client, Options: agent.CompactionOptions{ContextWindowTokens: 1000, Threshold: 0.5}}
	latest := api.ChatResponse{}
	latest.PromptEvalCount = 600
	result, err := compactor.MaybeCompact(context.Background(), agent.CompactionRequest{
		Model: "m",
		Messages: []api.Message{
			{Role: "user", Content: "a"},
			{Role: "assistant", Content: "b"},
			{Role: "user", Content: "c"},
		},
		Latest:       latest,
		ContinueTask: true,
	})
	if err != nil {
		t.Fatal(err)
	}
	if !result.Due || !result.Compacted {
		t.Fatalf("result = %#v, want due and compacted", result)
	}
	if result.Summary != "SUMMARY" {
		t.Fatalf("summary = %q, want SUMMARY", result.Summary)
	}
	if len(result.Messages) != 2 {
		t.Fatalf("compacted messages = %#v, want the summary pair", result.Messages)
	}
	assistantMsg, toolMsg := result.Messages[0], result.Messages[1]
	if assistantMsg.Role != "assistant" || len(assistantMsg.ToolCalls) != 1 ||
		assistantMsg.ToolCalls[0].Function.Name != agent.CompactionToolName ||
		assistantMsg.ToolCalls[0].ID != agent.CompactionToolCallID {
		t.Fatalf("assistant compaction message = %#v, want the canonical summary tool call", assistantMsg)
	}
	if toolMsg.Role != "tool" || toolMsg.ToolName != agent.CompactionToolName || toolMsg.ToolCallID != agent.CompactionToolCallID {
		t.Fatalf("tool compaction message = %#v, want the canonical summary tool result", toolMsg)
	}
	if !strings.HasPrefix(toolMsg.Content, agent.CompactionSummaryMessagePrefix) {
		t.Fatalf("tool content = %q, want the Conversation summary prefix", toolMsg.Content)
	}
	if !strings.Contains(toolMsg.Content, "SUMMARY") {
		t.Fatalf("tool content = %q, want the summary text", toolMsg.Content)
	}
	if !strings.Contains(toolMsg.Content, agent.CompactionContinueInstruction) {
		t.Fatalf("tool content = %q, want the continue instruction", toolMsg.Content)
	}
	if got := agent.CompactionSummaryText(toolMsg.Content); got != "SUMMARY" {
		t.Fatalf("CompactionSummaryText = %q, want SUMMARY", got)
	}
	if got, ok := agent.CompactionSummaryContent(toolMsg); !ok || got != "SUMMARY" {
		t.Fatalf("CompactionSummaryContent = %q, %v", got, ok)
	}
	if !agent.IsCompactionSummary(toolMsg) {
		t.Fatal("IsCompactionSummary must recognize the summary tool result")
	}
	if !agent.IsCompactionToolResult(toolMsg) {
		t.Fatal("IsCompactionToolResult must recognize the summary tool result")
	}
	if !agent.IsCompactionToolCall(assistantMsg) {
		t.Fatal("IsCompactionToolCall must recognize the summary assistant message")
	}
	if len(client.requests) != 1 {
		t.Fatalf("compaction requests = %d, want 1", len(client.requests))
	}
	compactionReq := client.requests[0]
	if len(compactionReq.Messages) != 2 || compactionReq.Messages[0].Role != "system" ||
		!strings.Contains(compactionReq.Messages[0].Content, "Summarize the archived part") {
		t.Fatalf("compaction request messages = %#v, want the summarization system prompt", compactionReq.Messages)
	}
	if compactionReq.Messages[1].Role != "user" || !strings.Contains(compactionReq.Messages[1].Content, "Messages to archive as JSON") {
		t.Fatalf("compaction request user body = %#v", compactionReq.Messages[1])
	}
}

func TestContractCompactorBelowThreshold(t *testing.T) {
	client := &scriptedClient{steps: []chatStep{{responses: contentResponses("SUMMARY")}}}
	compactor := &agent.SimpleCompactor{Client: client, Options: agent.CompactionOptions{ContextWindowTokens: 100000, Threshold: 0.8}}
	messages := []api.Message{{Role: "user", Content: "tiny"}, {Role: "assistant", Content: "small"}}
	result, err := compactor.MaybeCompact(context.Background(), agent.CompactionRequest{Model: "m", Messages: messages})
	if err != nil {
		t.Fatal(err)
	}
	if result.Compacted {
		t.Fatalf("result = %#v, want no compaction below threshold", result)
	}
	if len(result.Messages) != 2 || result.Messages[0].Content != "tiny" || result.Messages[1].Content != "small" {
		t.Fatalf("messages = %#v, want the original history untouched", result.Messages)
	}
	if len(client.requests) != 0 {
		t.Fatal("the model must not be called when compaction is not due")
	}
}

func TestContractResolveHelpers(t *testing.T) {
	if got := agent.ResolveContextWindowTokens(map[string]any{"num_ctx": 4096}, 1000); got != 4096 {
		t.Fatalf("num_ctx override = %d, want 4096", got)
	}
	if got := agent.ResolveContextWindowTokens(nil, 1234); got != 1234 {
		t.Fatalf("configured window = %d, want 1234", got)
	}
	if got := agent.ResolveContextWindowTokens(nil, 0); got != 32768 {
		t.Fatalf("default window = %d, want 32768", got)
	}
	if got := agent.ResolveCompactionThreshold(0.5); got != 0.5 {
		t.Fatalf("configured threshold = %v, want 0.5", got)
	}
	if got := agent.ResolveCompactionThreshold(0); got != 0.8 {
		t.Fatalf("default threshold = %v, want 0.8", got)
	}
}

func TestContractPreflightBudget(t *testing.T) {
	client := &scriptedClient{}
	recorder := &eventRecorder{}
	session := &agent.Session{
		Client:     client,
		EventSinks: []agent.EventSink{recorder},
		Compactor:  &agent.SimpleCompactor{Client: client, Options: agent.CompactionOptions{ContextWindowTokens: 1000}},
	}
	_, err := session.Run(context.Background(), agent.RunOptions{
		Model:    "m",
		Messages: []api.Message{{Role: "user", Content: strings.Repeat("x", 5000)}},
	})
	if err == nil || !strings.Contains(err.Error(), "prompt is too large for the current context") {
		t.Fatalf("error = %v, want the prompt-too-large error", err)
	}
	if len(client.requests) != 0 {
		t.Fatal("the model must not be called when the preflight budget check fails")
	}
	if !recorder.has(agent.EventError) {
		t.Fatal("want an error event for the preflight failure")
	}
}

// ---------------------------------------------------------------------------
// session-level compaction
// ---------------------------------------------------------------------------

func TestContractSessionAutoCompaction(t *testing.T) {
	first := api.ChatResponse{Message: api.Message{Role: "assistant", Content: "working on it"}}
	first.PromptEvalCount = 900
	client := &scriptedClient{steps: []chatStep{
		{responses: []api.ChatResponse{first}},
		{responses: contentResponses("SUM")},
	}}
	recorder := &eventRecorder{}
	session := &agent.Session{
		Client:     client,
		EventSinks: []agent.EventSink{recorder},
		Compactor:  &agent.SimpleCompactor{Client: client, Options: agent.CompactionOptions{ContextWindowTokens: 1000, Threshold: 0.8}},
	}
	result, err := session.Run(context.Background(), agent.RunOptions{
		Model:    "m",
		Messages: []api.Message{{Role: "user", Content: "hi"}},
	})
	if err != nil {
		t.Fatal(err)
	}
	toolMsgs := toolMessagesOf(result.Messages)
	if len(toolMsgs) != 1 || !strings.HasPrefix(toolMsgs[0].Content, "Conversation summary:\n") || !strings.Contains(toolMsgs[0].Content, "SUM") {
		t.Fatalf("messages = %#v, want the compacted summary pair", result.Messages)
	}
	if len(result.Messages) != 2 || result.Messages[0].Role != "assistant" || len(result.Messages[0].ToolCalls) != 1 {
		t.Fatalf("messages = %#v, want exactly the summary pair", result.Messages)
	}
	if !recorder.has(agent.EventCompactionStarted) {
		t.Fatal("want compaction_started event")
	}
	if !recorder.has(agent.EventCompacted) {
		t.Fatal("want compacted event")
	}
	if !recorder.hasStatus(agent.EventRunFinished, "done") {
		t.Fatal("want run_finished/done event")
	}
}

func TestContractToolOutputOverflowCompaction(t *testing.T) {
	tool := &fakeTool{name: "huge", result: agent.ToolResult{Content: strings.Repeat("z", 100000)}}
	registry := &agent.Registry{}
	registry.Register(tool)
	client := &scriptedClient{steps: []chatStep{
		approvalToolCallStep("call-1", "huge"),
		{responses: contentResponses("SUM2")},
		{responses: contentResponses("final answer")},
	}}
	recorder := &eventRecorder{}
	session := &agent.Session{
		Client:     client,
		Tools:      registry,
		EventSinks: []agent.EventSink{recorder},
		Compactor:  &agent.SimpleCompactor{Client: client, Options: agent.CompactionOptions{ContextWindowTokens: 500, Threshold: 0.8}},
	}
	result, err := session.Run(context.Background(), agent.RunOptions{
		Model:    "m",
		Messages: []api.Message{{Role: "user", Content: strings.Repeat("u", 1500)}},
	})
	if err != nil {
		t.Fatal(err)
	}
	if !recorder.hasStatus(agent.EventCompactionStarted, "tool_output") {
		t.Fatalf("want compaction_started/tool_output event, got %#v", recorder.all())
	}
	toolMsgs := toolMessagesOf(result.Messages)
	foundTruncated := false
	for _, msg := range toolMsgs {
		if strings.Contains(msg.Content, "[tool output truncated:") {
			foundTruncated = true
		}
	}
	if !foundTruncated {
		t.Fatalf("tool messages = %#v, want a truncated tool result after forced compaction", toolMsgs)
	}
	if result.Messages[len(result.Messages)-1].Content != "final answer" {
		t.Fatalf("final message = %#v, want the run to continue after forced compaction", result.Messages[len(result.Messages)-1])
	}
}
"""

GO_CONTRACT += r"""
// ---------------------------------------------------------------------------
// built-in bash tool
// ---------------------------------------------------------------------------

func TestContractBashTool(t *testing.T) {
	bash := &tools.Bash{}
	if bash.Name() != "bash" {
		t.Fatalf("Name() = %q, want bash", bash.Name())
	}
	if !agent.ToolRequiresApproval(bash, map[string]any{"command": "ls"}) {
		t.Fatal("bash must require approval")
	}
	schema := bash.Schema()
	if len(schema.Parameters.Required) != 1 || schema.Parameters.Required[0] != "command" {
		t.Fatalf("schema required = %v, want [command]", schema.Parameters.Required)
	}

	workDir := t.TempDir()
	result, err := bash.Execute(context.Background(), agent.ToolContext{WorkingDir: workDir}, map[string]any{"command": "echo hello"})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(result.Content, "hello") {
		t.Fatalf("content = %q, want command output", result.Content)
	}

	result, err = bash.Execute(context.Background(), agent.ToolContext{WorkingDir: workDir}, map[string]any{"command": "true"})
	if err != nil {
		t.Fatal(err)
	}
	if result.Content != "(no output)" {
		t.Fatalf("content = %q, want (no output)", result.Content)
	}

	result, err = bash.Execute(context.Background(), agent.ToolContext{WorkingDir: workDir}, map[string]any{"command": "echo oops >&2; exit 3"})
	if err != nil {
		t.Fatalf("a non-zero exit code must not be a Go error: %v", err)
	}
	if !strings.Contains(result.Content, "stderr:") || !strings.Contains(result.Content, "oops") {
		t.Fatalf("content = %q, want the stderr section", result.Content)
	}
	if !strings.Contains(result.Content, "Exit code: 3") {
		t.Fatalf("content = %q, want the exit code notice", result.Content)
	}

	if _, err := bash.Execute(context.Background(), agent.ToolContext{WorkingDir: workDir}, map[string]any{}); err == nil ||
		!strings.Contains(err.Error(), "command parameter is required") {
		t.Fatalf("missing command error = %v", err)
	}
}

func TestContractBashSafety(t *testing.T) {
	bash := &tools.Bash{}
	workDir := t.TempDir()
	for _, command := range []string{"rm -rf /", "rm -rf ~", "cat ~/.ssh/id_rsa", "cat /etc/shadow"} {
		if _, err := bash.Execute(context.Background(), agent.ToolContext{WorkingDir: workDir}, map[string]any{"command": command}); err == nil ||
			!strings.Contains(err.Error(), "refusing to run unsafe command") {
			t.Fatalf("command %q error = %v, want a refusal", command, err)
		}
	}
}

func TestContractBashWorkingDir(t *testing.T) {
	bash := &tools.Bash{}
	workDir := t.TempDir()
	subDir := filepath.Join(workDir, "sub")
	if err := os.Mkdir(subDir, 0o755); err != nil {
		t.Fatal(err)
	}
	result, err := bash.Execute(context.Background(), agent.ToolContext{WorkingDir: workDir}, map[string]any{"command": "cd sub"})
	if err != nil {
		t.Fatal(err)
	}
	if want := mustEvalSymlinks(t, subDir); result.WorkingDir != want {
		t.Fatalf("WorkingDir = %q, want %q after cd", result.WorkingDir, want)
	}
}

func TestContractBashOutputCap(t *testing.T) {
	bash := &tools.Bash{}
	result, err := bash.Execute(context.Background(), agent.ToolContext{WorkingDir: t.TempDir()}, map[string]any{
		"command": "head -c 70000 /dev/zero | tr '\\0' 'a'",
	})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(result.Content, "truncated: omitted ~") {
		t.Fatalf("content missing truncation notice: %.120q...", result.Content)
	}
	if len(result.Content) > 61000 {
		t.Fatalf("content length = %d bytes, want bounded near 60000", len(result.Content))
	}
}

// ---------------------------------------------------------------------------
// built-in file tools
// ---------------------------------------------------------------------------

func TestContractReadTool(t *testing.T) {
	read := &tools.Read{}
	if read.Name() != "read" {
		t.Fatalf("Name() = %q, want read", read.Name())
	}
	if !agent.ToolRequiresApproval(read, map[string]any{"path": "x"}) {
		t.Fatal("read must require approval")
	}

	workDir := t.TempDir()
	if err := os.WriteFile(filepath.Join(workDir, "lines.txt"), []byte("l1\nl2\nl3\nl4\nl5\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	result, err := read.Execute(context.Background(), agent.ToolContext{WorkingDir: workDir}, map[string]any{"path": "lines.txt"})
	if err != nil {
		t.Fatal(err)
	}
	if result.Content != "l1\nl2\nl3\nl4\nl5\n" {
		t.Fatalf("content = %q, want the full file", result.Content)
	}

	result, err = read.Execute(context.Background(), agent.ToolContext{WorkingDir: workDir}, map[string]any{
		"path": "lines.txt", "start": float64(2), "end": float64(3),
	})
	if err != nil {
		t.Fatal(err)
	}
	if result.Content != "l2\nl3\n" {
		t.Fatalf("selection = %q, want l2..l3 inclusive", result.Content)
	}

	if _, err := read.Execute(context.Background(), agent.ToolContext{WorkingDir: workDir}, map[string]any{"path": "/etc/hostname"}); err == nil ||
		!strings.Contains(err.Error(), "absolute paths are not allowed") {
		t.Fatalf("absolute path error = %v", err)
	}
	if _, err := read.Execute(context.Background(), agent.ToolContext{WorkingDir: workDir}, map[string]any{"path": "../escape.txt"}); err == nil ||
		!strings.Contains(err.Error(), "path escapes working directory") {
		t.Fatalf("escape error = %v", err)
	}
	if _, err := read.Execute(context.Background(), agent.ToolContext{WorkingDir: workDir}, map[string]any{"path": "missing.txt"}); err == nil {
		t.Fatal("reading a missing file must error")
	}
	if err := os.Mkdir(filepath.Join(workDir, "adir"), 0o755); err != nil {
		t.Fatal(err)
	}
	if _, err := read.Execute(context.Background(), agent.ToolContext{WorkingDir: workDir}, map[string]any{"path": "adir"}); err == nil ||
		!strings.Contains(err.Error(), "is a directory") {
		t.Fatalf("directory error = %v", err)
	}

	bigPath := filepath.Join(workDir, "big.txt")
	if err := os.WriteFile(bigPath, []byte(strings.Repeat("x", 200001)), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := read.Execute(context.Background(), agent.ToolContext{WorkingDir: workDir}, map[string]any{"path": "big.txt"}); err == nil ||
		!strings.Contains(err.Error(), "too large to read") {
		t.Fatalf("oversized file error = %v", err)
	}
}

func TestContractEditTool(t *testing.T) {
	edit := &tools.Edit{}
	if edit.Name() != "edit" {
		t.Fatalf("Name() = %q, want edit", edit.Name())
	}
	if !agent.ToolRequiresApproval(edit, map[string]any{"path": "x"}) {
		t.Fatal("edit must require approval")
	}

	workDir := t.TempDir()
	target := filepath.Join(workDir, "editme.txt")
	if err := os.WriteFile(target, []byte("foo bar foo\n"), 0o644); err != nil {
		t.Fatal(err)
	}

	result, err := edit.Execute(context.Background(), agent.ToolContext{WorkingDir: workDir}, map[string]any{
		"path": "editme.txt", "old_text": "bar", "new_text": "baz",
	})
	if err != nil {
		t.Fatal(err)
	}
	if result.Content != "Updated editme.txt (1 replacement)." {
		t.Fatalf("content = %q, want the update summary", result.Content)
	}
	data, _ := os.ReadFile(target)
	if string(data) != "foo baz foo\n" {
		t.Fatalf("file = %q, want the single replacement", data)
	}

	if _, err := edit.Execute(context.Background(), agent.ToolContext{WorkingDir: workDir}, map[string]any{
		"path": "editme.txt", "old_text": "foo", "new_text": "qux",
	}); err == nil || !strings.Contains(err.Error(), "matched 2 times") {
		t.Fatalf("multi-match error = %v", err)
	}

	result, err = edit.Execute(context.Background(), agent.ToolContext{WorkingDir: workDir}, map[string]any{
		"path": "editme.txt", "old_text": "foo", "new_text": "qux", "replace_all": true,
	})
	if err != nil {
		t.Fatal(err)
	}
	if result.Content != "Updated editme.txt (2 replacements)." {
		t.Fatalf("content = %q, want the replace-all summary", result.Content)
	}
	data, _ = os.ReadFile(target)
	if string(data) != "qux baz qux\n" {
		t.Fatalf("file = %q, want both replacements", data)
	}

	if _, err := edit.Execute(context.Background(), agent.ToolContext{WorkingDir: workDir}, map[string]any{
		"path": "editme.txt", "old_text": "nope", "new_text": "x",
	}); err == nil || !strings.Contains(err.Error(), "old_text was not found") {
		t.Fatalf("not-found error = %v", err)
	}

	linkPath := filepath.Join(workDir, "link.txt")
	if err := os.Symlink("editme.txt", linkPath); err != nil {
		t.Fatal(err)
	}
	if _, err := edit.Execute(context.Background(), agent.ToolContext{WorkingDir: workDir}, map[string]any{
		"path": "link.txt", "old_text": "qux", "new_text": "x",
	}); err == nil || !strings.Contains(err.Error(), "is a symlink") {
		t.Fatalf("symlink error = %v", err)
	}

	if _, err := edit.Execute(context.Background(), agent.ToolContext{WorkingDir: workDir}, map[string]any{
		"path": "/etc/hostname", "old_text": "a", "new_text": "b",
	}); err == nil || !strings.Contains(err.Error(), "absolute paths are not allowed") {
		t.Fatalf("absolute path error = %v", err)
	}
}

// ---------------------------------------------------------------------------
// built-in web tools
// ---------------------------------------------------------------------------

func TestContractWebSearchTool(t *testing.T) {
	search := &tools.WebSearch{}
	if search.Name() != "web_search" {
		t.Fatalf("Name() = %q, want web_search", search.Name())
	}
	if !agent.ToolRequiresApproval(search, map[string]any{"query": "x"}) {
		t.Fatal("web_search must require approval")
	}

	var gotQuery string
	var gotMaxResults int
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/experimental/web_search" {
			t.Fatalf("path = %q, want /api/experimental/web_search", r.URL.Path)
		}
		var req api.WebSearchRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			t.Fatal(err)
		}
		gotQuery = req.Query
		gotMaxResults = req.MaxResults
		w.Header().Set("Content-Type", "application/json")
		if req.Query == "nothing" {
			_, _ = w.Write([]byte(`{"results":[]}`))
			return
		}
		_, _ = w.Write([]byte(`{"results":[{"title":"T1","url":"https://x","content":"c1"}]}`))
	}))
	defer ts.Close()
	t.Setenv("OLLAMA_HOST", ts.URL)

	result, err := search.Execute(context.Background(), agent.ToolContext{}, map[string]any{"query": "ollama"})
	if err != nil {
		t.Fatal(err)
	}
	if gotQuery != "ollama" || gotMaxResults != 5 {
		t.Fatalf("server saw query=%q max_results=%d, want ollama/5", gotQuery, gotMaxResults)
	}
	if !strings.Contains(result.Content, "Search results for: ollama") ||
		!strings.Contains(result.Content, "1. T1") ||
		!strings.Contains(result.Content, "URL: https://x") {
		t.Fatalf("content = %q, want the formatted result list", result.Content)
	}

	result, err = search.Execute(context.Background(), agent.ToolContext{}, map[string]any{"query": "nothing"})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(result.Content, "No results found for query: nothing") {
		t.Fatalf("content = %q, want the no-results notice", result.Content)
	}

	if _, err := search.Execute(context.Background(), agent.ToolContext{}, map[string]any{}); err == nil ||
		!strings.Contains(err.Error(), "query parameter is required") {
		t.Fatalf("missing query error = %v", err)
	}

	authSrv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusUnauthorized)
		_, _ = w.Write([]byte(`{"error":"sign in required"}`))
	}))
	defer authSrv.Close()
	t.Setenv("OLLAMA_HOST", authSrv.URL)
	if _, err := search.Execute(context.Background(), agent.ToolContext{}, map[string]any{"query": "x"}); !errors.Is(err, tools.ErrWebSearchAuthRequired) {
		t.Fatalf("401 error = %v, want ErrWebSearchAuthRequired", err)
	}
}

func TestContractWebFetchTool(t *testing.T) {
	fetch := &tools.WebFetch{}
	if fetch.Name() != "web_fetch" {
		t.Fatalf("Name() = %q, want web_fetch", fetch.Name())
	}
	if !agent.ToolRequiresApproval(fetch, map[string]any{"url": "x"}) {
		t.Fatal("web_fetch must require approval")
	}

	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/experimental/web_fetch" {
			t.Fatalf("path = %q, want /api/experimental/web_fetch", r.URL.Path)
		}
		var req api.WebFetchRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			t.Fatal(err)
		}
		w.Header().Set("Content-Type", "application/json")
		if req.URL == "https://big.example" {
			payload, _ := json.Marshal(map[string]any{"title": "Big", "content": strings.Repeat("y", 60050)})
			_, _ = w.Write(payload)
			return
		}
		_, _ = w.Write([]byte(`{"title":"Page","content":"body text","links":["https://a"]}`))
	}))
	defer ts.Close()
	t.Setenv("OLLAMA_HOST", ts.URL)

	result, err := fetch.Execute(context.Background(), agent.ToolContext{}, map[string]any{"url": "https://page.example"})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(result.Content, "Title: Page") || !strings.Contains(result.Content, "Content:\nbody text") {
		t.Fatalf("content = %q, want title and content sections", result.Content)
	}

	result, err = fetch.Execute(context.Background(), agent.ToolContext{}, map[string]any{"url": "https://big.example"})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(result.Content, "[tool output truncated: showing first ~") || !strings.Contains(result.Content, "omitted ~") {
		t.Fatalf("long content missing truncation marker: %.200q...", result.Content)
	}

	if _, err := fetch.Execute(context.Background(), agent.ToolContext{}, map[string]any{}); err == nil ||
		!strings.Contains(err.Error(), "url parameter is required") {
		t.Fatalf("missing url error = %v", err)
	}

	authSrv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusUnauthorized)
		_, _ = w.Write([]byte(`{"error":"sign in required"}`))
	}))
	defer authSrv.Close()
	t.Setenv("OLLAMA_HOST", authSrv.URL)
	if _, err := fetch.Execute(context.Background(), agent.ToolContext{}, map[string]any{"url": "https://x"}); !errors.Is(err, tools.ErrWebFetchAuthRequired) {
		t.Fatalf("401 error = %v, want ErrWebFetchAuthRequired", err)
	}
}

// ---------------------------------------------------------------------------
// retired experimental CLI entrypoint
// ---------------------------------------------------------------------------

func TestContractRunCommandFlags(t *testing.T) {
	root := ollamacmd.NewCLI()
	runCmd, _, err := root.Find([]string{"run"})
	if err != nil || runCmd == nil || runCmd.Name() != "run" {
		t.Fatalf("run command not found: %v", err)
	}
	for _, flag := range []string{"experimental", "experimental-yolo", "experimental-websearch"} {
		if runCmd.Flags().Lookup(flag) != nil {
			t.Errorf("flag --%s should be retired from the run command", flag)
		}
	}
}
"""


def _go_env():
    env = dict(os.environ)
    goflags = env.get("GOFLAGS", "")
    if "-buildvcs=false" not in goflags:
        env["GOFLAGS"] = (goflags + " -buildvcs=false").strip()
    # Everything needed is already in the module cache baked into the image;
    # refuse network fetches so a missing cache entry fails fast and loudly.
    env["GOPROXY"] = "off"
    return env


def _tail(text, limit=6000):
    if len(text) <= limit:
        return text
    return "...<snip>...\n" + text[-limit:]


@pytest.fixture(scope="session", autouse=True)
def build_and_inject_contract_tests():
    """The whole tree must compile cleanly, then drop the Go contract tests in."""
    proc = subprocess.run(
        ["go", "build", "./..."],
        cwd=APP_DIR, capture_output=True, text=True,
        timeout=BUILD_TIMEOUT, env=_go_env(),
    )
    assert proc.returncode == 0, (
        "go build ./... failed:\n" + _tail(proc.stdout) + _tail(proc.stderr))

    os.makedirs(CONTRACT_DIR, exist_ok=True)
    with open(os.path.join(CONTRACT_DIR, "contract_test.go"), "w") as fh:
        fh.write(GO_CONTRACT)
    yield


def run_go_tests(pattern):
    """Run the Go contract tests matching pattern under /app."""
    proc = subprocess.run(
        ["go", "test", "-count=1", "-timeout", "15m", "-run", pattern,
         "./zz_harness_contract/"],
        cwd=APP_DIR, capture_output=True, text=True,
        timeout=GO_TEST_TIMEOUT, env=_go_env(),
    )
    assert proc.returncode == 0, (
        "go test -run '{0}' failed:\n{1}{2}".format(
            pattern, _tail(proc.stdout), _tail(proc.stderr)))


def test_api_client_web_endpoints(build_and_inject_contract_tests):
    run_go_tests("^TestContractAPIWeb(Search|Fetch)$")


def test_registry_contract(build_and_inject_contract_tests):
    run_go_tests("^TestContractRegistry$")


def test_event_wire_format(build_and_inject_contract_tests):
    run_go_tests("^TestContractEventWireFormat$")


def test_session_run_and_request(build_and_inject_contract_tests):
    run_go_tests("^TestContractSession(RunBasics|RequestConstruction|Validation)$")


def test_session_tool_loop(build_and_inject_contract_tests):
    run_go_tests("^TestContractSession(ToolLoop|UnknownTool|ToolError)$")


def test_session_limits_cancellation_retry(build_and_inject_contract_tests):
    run_go_tests("^TestContractSession(MaxToolRounds|Cancellation|ServerErrorRetry)$")


def test_approval_flow(build_and_inject_contract_tests):
    run_go_tests("^TestContract(Approval|NoApprovalForPlainTool)")


def test_working_dir_tracking(build_and_inject_contract_tests):
    run_go_tests("^TestContractWorkingDirTracking$")


def test_tool_result_truncation(build_and_inject_contract_tests):
    run_go_tests("^TestContractToolResultTruncation")


def test_compactor(build_and_inject_contract_tests):
    run_go_tests("^TestContract(SimpleCompactorWireFormat|CompactorBelowThreshold|ResolveHelpers|PreflightBudget)$")


def test_session_compaction(build_and_inject_contract_tests):
    run_go_tests("^TestContract(SessionAutoCompaction|ToolOutputOverflowCompaction)$")


def test_bash_tool(build_and_inject_contract_tests):
    run_go_tests("^TestContractBash(Tool|Safety|WorkingDir|OutputCap)$")


def test_file_tools(build_and_inject_contract_tests):
    run_go_tests("^TestContract(Read|Edit)Tool$")


def test_web_tools(build_and_inject_contract_tests):
    run_go_tests("^TestContractWeb(Search|Fetch)Tool$")


def test_run_command_flags_retired(build_and_inject_contract_tests):
    run_go_tests("^TestContractRunCommandFlags$")
