# 02. Thiết kế Plug & Play (Protocols, Adapters & Wiring)

Nguyên tắc cốt lõi: **Lõi `AgentLoop` chỉ nói chuyện với Interface (`Protocol`) và `Canonical Models`**. Việc chọn dùng model nào, tool nào, MCP nào hay memory nào được quyết định hoàn toàn tại `HarnessBuilder` dựa trên file cấu hình.

---

## 1. Canonical Data Models (`src/harness/core/models.py`)

Mọi adapter đều phải dịch qua lại với bộ schema chuẩn hóa bằng `Pydantic v2`:
- `Message`: `role` (`system | user | assistant | tool`), `content` (text hoặc multimodal parts), `tool_calls`, `tool_call_id`.
- `ToolSpec`: `name`, `description`, `parameters_schema` (JSON Schema), `requires_approval: bool`.
- `ToolCall` / `ToolResult`: `id`, `name`, `arguments: dict`, `output: str | dict`, `is_error: bool`.
- `ModelConfig`: `provider`, `model`, `temperature`, `max_tokens`, `provider_options: dict[str, Any]` *(pass-through cho tính năng đặc thù như Thinking budget hay Context Caching của từng hãng)*.
- `ModelResponse`: `content`, `tool_calls`, `usage` (`input_tokens`, `output_tokens`, `cost_usd`).
- `AgentEvent`: `Thinking`, `TokenDelta`, `ToolStart`, `ToolEnd`, `ApprovalRequired`, `FinalAnswer`, `Error`.

---

## 2. Bộ não AI — `LLMProvider` (`src/harness/providers/`)

```python
from typing import AsyncIterator, Protocol

class LLMProvider(Protocol):
    async def generate(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        config: ModelConfig | None = None,
    ) -> ModelResponse: ...

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        config: ModelConfig | None = None,
    ) -> AsyncIterator[AgentEvent]: ...
```

- **Registry:** `ProviderRegistry` ánh xạ chuỗi `"gemini"`, `"openai"`, `"litellm"`, `"ollama"` sang Adapter tương ứng.
- **Pass-through Options:** Không ép chuẩn hóa các tính năng đặc thù của từng hãng nếu làm mất sức mạnh của model; cho phép truyền qua `config.provider_options`.

---

## 3. Custom Tools — `@tool` & `ToolRegistry` (`src/harness/tools/`)

- **Decorator `@tool`:** Tự động đọc tên hàm, docstring và type hints / Pydantic model để sinh `ToolSpec` (JSON Schema) và tự động validate tham số đầu vào trước khi thực thi.
  ```python
  @tool(name="query_orders", requires_approval=False)
  async def query_orders(customer_id: str, limit: int = 10) -> dict:
      """Tra cứu đơn hàng gần nhất của khách hàng."""
      ...
  ```
- **`ToolRegistry`:** Quản lý tập trung cả **Local Python Tools** và **MCP Tools** dưới cùng một giao diện `execute(name, arguments) -> ToolResult`.

---

## 4. MCP Bridge (`src/harness/tools/mcp_bridge.py`)

- Kết nối tới các MCP Server qua `stdio` (local process) hoặc `SSE / Streamable HTTP` (remote server).
- Gọi `list_tools()` từ MCP Server, bọc (wrap) từng MCP tool thành một `Tool` chuẩn của Harness và nạp vào `ToolRegistry`.
- Với `AgentLoop`, gọi một MCP tool (ví dụ Postgres, GitHub, Filesystem) **giống hệt** gọi một hàm Python nội bộ.

---

## 5. Middleware / Lifecycle Hooks (`src/harness/core/hooks.py`)

Cho phép cắm-rút các tính năng cắt ngang (cross-cutting concerns) mà không sửa code lõi:
- `before_model_call(state, messages)` -> Nén context, lọc PII, kiểm tra ngân sách.
- `after_model_call(state, response)` -> Ghi nhận token usage, latency, trace.
- `before_tool_call(state, tool_call)` -> Kiểm tra quyền (sandbox), kích hoạt Human-in-the-loop.
- `after_tool_call(state, tool_result)` -> Cắt gọn output quá dài (Output Offloading).
- `on_error(state, error)` -> Kích hoạt cơ chế Retry hoặc Reflector (tự học từ lỗi).

---

## 6. Cấu hình & Lắp ghép (`configs/default.yaml` + `HarnessBuilder`)

```yaml
llm:
  provider: "gemini"
  model: "gemini-2.5-pro"
  temperature: 0.2
  provider_options: {}

limits:
  max_steps: 15
  max_tokens: 100000
  timeout_seconds: 120

memory:
  short_term: "in_memory"     # hoặc "redis"
  long_term: "in_memory"      # hoặc "pgvector" / "qdrant"
  compaction_threshold: 0.75  # Nén context khi đạt 75% ngưỡng

agents_dir: ".agent"         # Tự động nạp rules, skills, mcp_config.json
```

`HarnessBuilder.from_config("configs/default.yaml")` sẽ khởi tạo toàn bộ các Adapter, nạp `.agent/`, đăng ký `app/tools/`, gắn `Hooks` và trả về một `Agent` sẵn sàng chạy.
