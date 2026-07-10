# DeepSeek Model Configuration

Ops Agent Chat V1 uses DeepSeek through an OpenAI-compatible API client.

According to the official DeepSeek API docs, the OpenAI-compatible base URL is:

```text
https://api.deepseek.com
```

V1 model:

```text
deepseek-v4-pro
```

## Environment Variables

```env
DEEPSEEK_API_KEY=replace-with-your-key
DEEPSEEK_BASE_URL=https://api.deepseek.com
LLM_PROVIDER=deepseek
LLM_MODEL=deepseek-v4-pro
LLM_REASONING_EFFORT=high
LLM_THINKING_ENABLED=true
```

## Backend Client Shape

The backend should use an OpenAI-compatible client with:

```text
base_url = DEEPSEEK_BASE_URL
api_key = DEEPSEEK_API_KEY
model = LLM_MODEL
```

For V1, do not enable streaming.

```text
stream = false
```

## Recommended Usage By Agent Node

| Node | Model setting |
|---|---|
| IntentRouter | `deepseek-v4-pro`, JSON output, low temperature |
| CommandAgent | `deepseek-v4-pro`, JSON output, low temperature |
| RAG Answer | `deepseek-v4-pro`, normal answer, citations required |
| ResultAnalyzer | `deepseek-v4-pro`, evidence-constrained answer |

## JSON Output Requirement

CommandAgent must return structured JSON, not prose.

Example:

```json
{
  "goal": "Check why VideoHub is unavailable",
  "commands": [
    {
      "command": "docker compose -f docker-compose.yml ps",
      "purpose": "Check Docker Compose service status",
      "expected_risk_hint": "read_only"
    }
  ]
}
```

The backend should validate this JSON with Pydantic before RuleGuard runs.

