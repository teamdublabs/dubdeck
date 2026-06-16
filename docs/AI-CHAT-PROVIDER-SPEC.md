# AI Chat Provider for Dubdeck — Specification
**Author:** N1H Tech (Gemma)  
**Date:** 2026-06-13  
**Status:** Draft  
**Ref:** `docs/XCP-NG-PROVIDER-SPEC.md` (parallel spec for XCP-ng provider)

---

## Overview

Dubdeck's desktop shell gets an **ChatApp** — a first-class window app alongside GroupApp, OpsLogApp, and SettingsApp. ChatApp provides AI chat backed by a pluggable provider architecture. The first provider is Ollama (local, sovereign, offline-capable). Additional providers (MiniMax, OpenAI, Anthropic, Google, Groq, Cohere) are added by implementing the same interface.

**Design principle:** Ollama is always the primary. External providers are pluggable backups — for users without local GPU hardware, as a failover when Ollama is offline, or for capability gaps (e.g., vision, large context). The architecture makes adding new providers trivial.

---

## Architecture

### Provider Interface

```python
class ChatProvider(Protocol):
    """Interface every AI chat provider implements."""

    type_name: ClassVar[str]
    default_model: str

    async def chat(
        self,
        prompt: str,
        model: str | None = None,
        system_prompt: str | None = None,
        stream: bool = True,
    ) -> AsyncGenerator[str, None]:
        """Stream chat completions. Yields tokens as they arrive."""
        ...

    async def list_models(self) -> list[str]:
        """List available models for this provider."""
        ...
```

Each provider:
- Handles its own API key via env var reference (never in browser)
- Formats requests in the provider's native format
- Streams responses as SSE to the frontend
- Exposes the same `chat()` interface

### Backend Routing

```
POST /api/chat
{
  "prompt": "...",
  "provider": "ollama" | "minimax" | "openai" | "anthropic" | "google" | "groq" | "cohere",
  "model": "qwen3.6-35b-a3b",   # optional, falls back to provider default
  "system_prompt": "...",       # optional infrastructure context
  "stream": true
}
```

**Response:** Server-Sent Events (SSE), one token per event:
```
data: {"token": "Hello"}
data: {"token": " world"}
data: {"token": "!"}
data: [DONE]
```

**Non-streaming fallback:** `stream: false` → returns `{"response": "...", "model": "...", "usage": {...}}`

### Config Schema

```yaml
ai_providers:
  ollama:
    id: local-ollama
    url: "http://__OLLAMA_IP__:11434"
    default_model: "qwen3.6-35b-a3b"
    # No API key — LAN-only, no auth needed

  minimax:
    id: minimax
    api_key_env: MINIMAX_API_KEY
    api_base: "https://api.minimax.chat/v1"
    default_model: "MiniMax-Text-01"

  openai:
    id: openai
    api_key_env: OPENAI_API_KEY
    api_base: "https://api.openai.com/v1"
    default_model: "gpt-4o"

  anthropic:
    id: anthropic
    api_key_env: ANTHROPIC_API_KEY
    api_base: "https://api.anthropic.com/v1"
    default_model: "claude-sonnet-4-20250514"
    # Note: export control risk — see § export-control-risk

  google:
    id: google
    api_key_env: GOOGLE_API_KEY
    api_base: "https://generativelanguage.googleapis.com/v1beta"
    default_model: "gemini-2.0-flash"

  groq:
    id: groq
    api_key_env: GROQ_API_KEY
    api_base: "https://api.groq.com/openai/v1"
    default_model: "llama-3.3-70b-versatile"

  cohere:
    id: cohere
    api_key_env: COHERE_API_KEY
    api_base: "https://api.cohere.ai/v1"
    default_model: "command-r-plus"
```

---

## Providers

### OllamaProvider

**Type:** `ollama`  
**Auth:** None (LAN-only)  
**API:** `POST /api/generate` or `POST /api/chat`  
**Streaming:** `stream: true` → SSE token stream  
**Env vars:** None

```python
class OllamaProvider(ChatProvider):
    type_name = "ollama"
    default_model = "qwen3.6-35b-a3b"

    async def chat(self, prompt, model=None, system_prompt=None, stream=True):
        payload = {
            "model": model or self.default_model,
            "prompt": (system_prompt + "\n\n" if system_prompt else "") + prompt,
            "stream": stream,
        }
        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream("POST", f"{self.url}/api/generate", json=payload) as r:
                async for line in r.aiter_lines():
                    if line.startswith("data: "):
                        data = json.loads(line[6:])
                        if data.get("response"):
                            yield data["response"]
                        if data.get("done"):
                            break
```

**Special handling:** Ollama's `/api/generate` takes a single `prompt` string. System prompt is prepended. For multi-turn chat (conversation history), maintain a message list and format as Ollama's `/api/chat` format.

**Model list:** `GET /api/tags` → parse `models[].name`

---

### MiniMaxProvider

**Type:** `minimax`  
**Auth:** API key via env var  
**API:** `POST /v1/text/chatcompletion_v2`  
**Streaming:** `stream: true` → SSE  
**Env vars:** `MINIMAX_API_KEY`

```python
class MiniMaxProvider(ChatProvider):
    type_name = "minimax"
    default_model = "MiniMax-Text-01"

    async def chat(self, prompt, model=None, system_prompt=None, stream=True):
        headers = {
            "Authorization": f"Bearer {os.environ['MINIMAX_API_KEY']}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model or self.default_model,
            "messages": (
                [{"role": "system", "content": system_prompt}]
                if system_prompt
                else []
            ) + [{"role": "user", "content": prompt}],
            "stream": stream,
        }
        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream(
                "POST",
                f"{self.api_base}/text/chatcompletion_v2",
                headers=headers,
                json=payload,
            ) as r:
                async for line in r.aiter_lines():
                    if line.startswith("data: "):
                        raw = line[6:]
                        if raw == "[DONE]":
                            break
                        data = json.loads(raw)
                        if choices := data.get("choices"):
                            if delta := choices[0].get("delta", {}).get("content"):
                                yield delta
```

---

### OpenAIProvider

**Type:** `openai`  
**Auth:** API key via env var  
**API:** `POST /chat/completions`  
**Streaming:** `stream: true` → SSE  
**Env vars:** `OPENAI_API_KEY`

Same pattern as MiniMax. Provider `model` field maps to OpenAI `model` parameter.

---

### AnthropicProvider — ⚠️ Export Control Risk

**Type:** `anthropic`  
**Auth:** API key via env var  
**API:** `POST /v1/messages`  
**Streaming:** `stream: true` → SSE (`event: content_block_delta`)  
**Env vars:** `ANTHROPIC_API_KEY`

**Critical warning:** As of 2026-06-12, Anthropic's Fable 5 and Mythos 5 were globally suspended via US export control directive. While other Claude models (Opus 4.8, Sonnet 4.6, Haiku 4.5) remain available, the precedent is set. Any US-hosted frontier model is a candidate for future export controls. **Use Anthropic as a backup provider only, not primary.** Do not build critical workflows that depend on it.

```python
class AnthropicProvider(ChatProvider):
    type_name = "anthropic"
    default_model = "claude-sonnet-4-20250514"

    async def chat(self, prompt, model=None, system_prompt=None, stream=True):
        headers = {
            "x-api-key": os.environ["ANTHROPIC_API_KEY"],
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": model or self.default_model,
            "messages": [{"role": "user", "content": prompt}],
            "system": system_prompt,
            "stream": stream,
        }
        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream(
                "POST",
                f"{self.api_base}/v1/messages",
                headers=headers,
                json=payload,
            ) as r:
                async for line in r.aiter_lines():
                    if line.startswith("event: content_block_delta"):
                        data = json.loads(line.split("data: ", 1)[1])
                        if delta := data.get("delta", {}).get("text"):
                            yield delta
```

---

### GoogleProvider

**Type:** `google`  
**Auth:** API key via env var (`GOOGLE_API_KEY`)  
**API:** `POST /models/{model}:generateContent`  
**Streaming:** `stream: true` → SSE  
**Env vars:** `GOOGLE_API_KEY`

---

### GroqProvider

**Type:** `groq`  
**Auth:** API key via env var  
**API:** `POST /chat/completions` (OpenAI-compatible)  
**Streaming:** SSE  
**Env vars:** `GROQ_API_KEY`

Groq is particularly interesting as a backup — free tier available, fast inference, no GPU required server-side.

---

### CohereProvider

**Type:** `cohere`  
**Auth:** API key via env var  
**API:** `POST /v1/chat`  
**Streaming:** SSE  
**Env vars:** `COHERE_API_KEY`

---

## Frontend — ChatApp

### File Layout

```
frontend/src/apps/
├── ChatApp.tsx           ← main app component
├── ChatMessage.tsx       ← individual message bubble
├── ModelSelector.tsx     ← provider + model dropdown
└── ChatInput.tsx         ← textarea with Enter to send
```

### UI Layout

```
┌─────────────────────────────────────────────────────┐
│ Chat                                          [─][□][×]│
├─────────────────────────────────────────────────────┤
│ Provider: [Ollama ▼]  Model: [qwen3.6-35b ▼]        │
├─────────────────────────────────────────────────────┤
│                                                     │
│  🤖 Here's your lab status: 78 VMs, 13 running... │
│                                                     │
│  👤 start the research lab group                   │
│                                                     │
│  🤖 Starting research-lab VMs... [streaming]        │
│                                                     │
├─────────────────────────────────────────────────────┤
│ [system prompt] Context: __XCPNG_HOST__, __XCPNG_HOST__, research-lab  │
├─────────────────────────────────────────────────────┤
│ [Type a message...                          ] [Send]│
└─────────────────────────────────────────────────────┘
```

### Component Details

**ModelSelector:**
- First dropdown: provider (Ollama, MiniMax, OpenAI, Anthropic, Google, Groq, Cohere)
- Second dropdown: models available for selected provider (populated from `/api/chat/models?provider=ollama`)
- Persists selection in localStorage

**ChatMessage:**
- Role badge: "🤖 AI" vs "👤 You"
- Timestamp on each message
- Copy button on hover
- For AI messages: stream tokens in real-time as they arrive

**ChatInput:**
- Textarea, auto-resize up to 8 lines
- Enter to send, Shift+Enter for newline
- Disabled while streaming
- Shows streaming indicator when AI is responding

**System Prompt Display:**
- Collapsible section below messages
- Shows current infrastructure context
- Editable — user can customize the system prompt
- Saved to localStorage per provider

### API Calls

```typescript
// Send message
POST /api/chat
{ prompt: string, provider: string, model?: string, system_prompt?: string, stream: true }

// SSE response stream
// Backend proxies to provider and streams tokens back

// List available models for a provider
GET /api/chat/models?provider=ollama
// Returns: { provider: string, models: string[], default: string }
```

### Backend Route

```python
# /api/chat — POST
@app.post("/api/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    provider = get_chat_provider(req.provider)
    return StreamingResponse(
        provider.chat(req.prompt, req.model, req.system_prompt, req.stream),
        media_type="text/event-stream",
    )

# /api/chat/models — GET
@app.get("/api/chat/models")
async def list_models(provider: str) -> dict:
    p = get_chat_provider(provider)
    return {"provider": provider, "models": await p.list_models(), "default": p.default_model}
```

---

## Infrastructure Context System Prompt

Auto-generated from Dubdeck's live state for every chat session:

```python
def build_infrastructure_context(config: Config, status: StatusSnapshot) -> str:
    hosts = list(status.hosts.keys())
    running = sum(1 for g in status.groups.values() for r in g.resources if r.state == "running")
    total = sum(len(g.resources) for g in status.groups.values())
    groups = list(config.groups.keys())

    return f"""You are connected to lab infrastructure:
- Hosts: {', '.join(hosts)}
- VMs: {total} total, {running} running
- Groups: {', '.join(groups)}
- Active alerts: {get_alerts(status)}
- Time: {datetime.now().isoformat()}

You can discuss this infrastructure but all VM operations require explicit user confirmation."""
```

This is passed as `system_prompt` to the AI provider on every request.

---

## Security Considerations

### API Keys
- Stored as env var names in config, never as values
- Backend reads env vars at startup; missing keys → provider disabled with warning
- Browser never sees API keys

### Export Control (Anthropic)
- AnthropicProvider flagged with export control warning in UI
- Shown to user when Anthropic is selected as provider
- Configurable allow/disable per provider via settings

### Prompt Injection
- User prompts are sent to external providers (OpenAI, Anthropic, Google)
- System prompt is controlled by backend, not user-submitted
- No direct user prompt → model parameters without context wrapping

### Rate Limiting
- Per-provider rate limits inherited from provider API limits
- Ollama: no limit (LAN)
- External: implement per-key rate limiting in provider class

---

## Priority Implementation Order

| Phase | Provider | Rationale |
|---|---|---|
| 1 | Ollama | Primary — local, sovereign, offline-capable |
| 2 | MiniMax | External backup, weekly allowance (resets 2026-05-18) |
| 3 | OpenAI | Widely used, GPT-4o is strong general model |
| 4 | Groq | Fast, free tier, no GPU dependency |
| 5 | Google | Gemini 2.0 is competitive |
| 6 | Cohere | Command R+ for retrieval-augmented use cases |
| 7 | Anthropic | **Last** — export control precedent is a real risk |

---

## Provider Registry

```python
# backend/app/chat_providers/__init__.py

CHAT_PROVIDER_TYPES: dict[str, type[ChatProvider]] = {
    "ollama": OllamaProvider,
    "minimax": MiniMaxProvider,
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
    "google": GoogleProvider,
    "groq": GroqProvider,
    "cohere": CohereProvider,
}

def get_chat_provider(name: str, config: AIProviderConfig) -> ChatProvider:
    cls = CHAT_PROVIDER_TYPES.get(name)
    if cls is None:
        raise ValueError(f"unknown chat provider {name!r}")
    return cls(config)
```

---

## Testing

```python
# FakeChatProvider for tests — mirrors FakeTransport pattern
class FakeChatProvider(ChatProvider):
    type_name = "fake"
    default_model = "fake-model"

    def __init__(self, responses: list[str]):
        self._responses = responses

    async def chat(self, prompt, model=None, system_prompt=None, stream=True):
        for token in self._responses[0]:
            yield token

    async def list_models(self):
        return ["fake-model"]
```

All tests use `FakeChatProvider` — no real API calls in CI.

---

## Export Control Risk Note (2026-06-13)

> The Fable 5 ban (2026-06-12) demonstrated that US-hosted frontier models can be pulled globally via export control with no warning. Any provider whose infrastructure runs through US jurisdiction is exposed. This includes Anthropic, Google, and any model hosted on US cloud infrastructure.
>
> Ollama (local, self-hosted) is the only fully sovereign option. External providers should be treated as disposable backups — valuable for capability gaps, but not for critical infrastructure.
>
> This is not a theoretical risk. It is the current operating environment.
