# 06. Hướng dẫn Sử dụng Repo Template (`harness-ai` v1)

Tài liệu này hướng dẫn cách cài đặt, cấu hình, mở rộng và vận hành bộ khung **Harness AI (v1)** cho một dự án mới.

---

## 1. Cài đặt & Chạy thử nhanh (Quickstart)

### Bước 1: Tạo dự án mới từ Template & Cài dependencies
Sau khi clone hoặc tạo repo mới từ GitHub Template:

```bash
# Cài đặt ở chế độ editable kèm dev tools (pytest, ruff)
pip install -e ".[dev]"

# Cài thêm adapter Gemini và MCP nếu cần chạy với LLM thật / MCP server
pip install -e ".[gemini,mcp,dev]"
```

> **Lưu ý:** Cần cài `pip install -e .` để Python nhận diện được các package `harness` và `app` khi chạy CLI hoặc `evals` trực tiếp bằng `python3 -m ...`.

### Bước 2: Cấu hình biến môi trường
```bash
cp .env.example .env
# Điền GEMINI_API_KEY=... vào file .env nếu dùng provider "gemini"
```

### Bước 3: Kiểm tra hệ thống
```bash
# 1. Chạy toàn bộ unit tests (50 tests)
python3 -m pytest -v

# 2. Kiểm tra lint
python3 -m ruff check .

# 3. Chạy CLI demo với MockLLMProvider (mặc định trong configs/default.yaml)
python3 -m app.main "Check status for billing-api"

# 4. Chạy bộ đánh giá offline (Evals)
python3 -m evals.run_eval
```

---

## 2. Cấu trúc Thư mục & Ranh giới Code

```text
├── configs/default.yaml     # Cấu hình LLM, limits, memory, observability, agents_dir
├── .agent/                  # Rules, Skills và MCP config (dùng chung cho Antigravity 2.0 & runtime)
├── src/
│   ├── harness/             # LÕI BẤT BIẾN (KHÔNG được import từ src/app/)
│   └── app/                 # NGHIỆP VỤ DỰ ÁN (viết tool, prompt, API/CLI ở đây)
├── evals/                   # Dataset JSONL, scorers và runner đánh giá
├── traces/                  # Snapshot JSONL theo từng run_id (phục vụ debug/replay)
└── tests/                   # Unit tests cho lõi harness
```

> **Quy tắc vàng:** Mọi code nghiệp vụ của dự án mới phải đặt trong `src/app/` (hoặc package riêng của bạn). Tuyệt đối không sửa `src/harness/` để import ngược từ `src/app/`, nhằm giữ khả năng tách `src/harness/` thành Python Package độc lập ở Giai đoạn 2.

---

## 3. Cấu hình hệ thống (`configs/default.yaml`)

File `configs/default.yaml` điều khiển toàn bộ hành vi của `HarnessBuilder`:

```yaml
llm:
  provider: "mock"             # "mock" hoặc "gemini" (hoặc provider tự đăng ký)
  model: "gemini-2.5-flash"
  temperature: 0.2
  max_tokens: 4096
  provider_options: {}         # Truyền thẳng option riêng của SDK (vd: thinking_config)

limits:
  max_steps: 15                # Giới hạn số vòng lặp LLM/Tool trong 1 turn
  max_tokens: 100000           # Tổng token tối đa của session
  max_cost_usd: 1.0            # Ngân sách tối đa (USD) của session
  timeout_seconds: 120         # Timeout cho mỗi lần gọi model/tool
  turn_timeout_seconds: 300    # Timeout cho toàn bộ 1 turn
  max_repeated_tool_calls: 3   # Ngắt mạch nếu lặp y hệt cùng tool + arguments N lần
  max_retries: 2               # Số lần retry khi gặp lỗi transient (429, 5xx, timeout)
  retry_backoff_seconds: 0.5

memory:
  short_term_window: 20              # Số message giữ lại trong cửa sổ ngắn hạn
  compaction_threshold_messages: 30  # Ngưỡng tự động tóm tắt lịch sử bằng LLM
  tool_output_max_chars: 4000        # Vượt ngưỡng này sẽ cắt gọn và lưu ra offload_dir
  offload_dir: "traces/offload"
  # persist_dir: "data/memory"       # Bỏ comment để dùng JSONFileMemoryStore lưu ra đĩa

observability:
  trace_dir: "traces"
  enable_tracing: true

agents_dir: ".agent"
```

---

## 4. Viết & Đăng ký Tools (`src/app/tools/`)

Dùng decorator `@tool` từ `harness.tools.decorator` để biến bất kỳ hàm Python đồng bộ (`def`) hoặc bất đồng bộ (`async def`) nào thành tool chuẩn cho Agent:

```python
from harness.tools.decorator import tool
from harness.tools.registry import ToolRegistry

@tool(description="Tra cứu trạng thái dịch vụ theo tên.")
def get_project_status(service_name: str) -> dict[str, str]:
    return {"service": service_name, "status": "healthy"}

@tool(
    description="Xóa bản ghi theo ID (yêu cầu người dùng duyệt).",
    requires_approval=True,
)
def delete_record(record_id: str) -> dict[str, str]:
    return {"deleted": record_id, "status": "ok"}

registry = ToolRegistry()
registry.register_many([get_project_status, delete_record])
```

- Hàm `def` đồng bộ sẽ tự động được `ToolRegistry` chạy qua `asyncio.to_thread` để không block event loop.
- Tham số đầu vào được tự động validate bằng Pydantic trước khi hàm chạy.

---

## 5. Các Cách Chạy `AgentLoop`

### A. Streaming Events theo thời gian thực (Khuyên dùng cho CLI / Web UI)
```python
from harness.core.builder import HarnessBuilder

agent = await HarnessBuilder.from_yaml("configs/default.yaml").abuild(
    tool_registry=registry,
    base_system_prompt="You are a helpful AI assistant.",
)

async with agent:
    async for event in agent.stream(user_input="Check status for billing-api"):
        if event.type == "thinking":
            print(f"[thinking] {event.content}")
        elif event.type == "token_delta":
            print(event.content, end="", flush=True)
        elif event.type == "tool_start":
            print(f"\n[tool_start] {event.tool_call.name}")
        elif event.type == "tool_end":
            print(f"[tool_end] {event.tool_result.output}")
        elif event.type == "final_answer":
            print("\nDone:", event.content)
        elif event.type == "error":
            print("\nError:", event.content)
```

### B. Multi-turn Session & Human-in-the-Loop (Pause / Resume)
Truyền cùng một đối tượng `SessionState` qua nhiều lượt gọi. Khi gặp tool có `requires_approval=True`, `AgentLoop` sẽ dừng và trả về `event.type == "approval_required"`:

```python
from harness.core.state import SessionState

state = SessionState()
event = await agent.run(user_input="Delete record rec-123", state=state)

if event.type == "approval_required":
    # Sau khi người dùng xác nhận (True = đồng ý, False = từ chối)
    event = await agent.run(state=state, approve_pending=True)
```

### C. Structured Output (Ép đầu ra theo Pydantic Model)
Nếu cần Agent trả về dữ liệu có cấu trúc thay vì văn bản tự do, truyền `response_schema`. Nếu model trả sai JSON, `AgentLoop` sẽ tự động gửi lỗi Pydantic cho model tự sửa (tối đa 2 lần):

```python
from pydantic import BaseModel

class HealthReport(BaseModel):
    service: str
    is_healthy: bool
    summary: str

event = await agent.run(
    user_input="Check billing-api",
    response_schema=HealthReport,
)
report: HealthReport = event.parsed_output
```

---

## 6. Plug & Play: Thêm LLM Provider, MCP & `.agent/`

### Đổi hoặc thêm LLM Provider
- **Dùng Gemini:** Đổi `llm.provider: "gemini"` trong `configs/default.yaml` và set `GEMINI_API_KEY`.
- **Thêm Provider mới (vd: OpenAI, Ollama):** Viết class implement protocol `LLMProvider` (`generate` và `stream` trong `src/harness/providers/base.py`), sau đó đăng ký với `ProviderRegistry.register("openai", factory)`.

### Tích hợp `.agent/` (Antigravity 2.0)
Khi `agents_dir: ".agent"` được bật trong YAML, `AgentsDirLoader` tự động nạp:
- **Rules (`.agent/rules/*.md`):** Các file có `trigger: always_on` (hoặc không khai báo `trigger`) sẽ được nối vào System Prompt.
- **Skills (`.agent/skills/<name>/SKILL.md`):** Chỉ đọc `name` và `description` từ YAML frontmatter để liệt kê vào System Prompt. `HarnessBuilder` tự đăng ký tool `read_skill` (có sandbox chống path traversal) để Agent tự đọc toàn bộ `SKILL.md` khi cần.
- **MCP Servers (`.agent/mcp_config.json`):** Khai báo server trong trường `mcpServers` (hoặc mục `mcp_servers` trong YAML):
  ```json
  {
    "mcpServers": {
      "filesystem": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "./workspace"]
      }
    }
  }
  ```
  > **Quan trọng:** Khi dùng MCP, luôn khởi tạo agent bằng `await builder.abuild(...)` (hoặc `build_async`) và bọc trong `async with agent:` để tự động đóng kết nối subprocess MCP khi kết thúc.

---

## 7. Bộ nhớ, Tự học (`ReflectorHook`) & Evals

- **Memory Persistence:** Mặc định `HarnessBuilder` dùng `InMemoryStore`. Để lưu `SessionState` và `EpisodeRecord` xuống đĩa, thêm `persist_dir: "data/memory"` vào mục `memory` trong `configs/default.yaml`.
- **Self-learning (`ReflectorHook`):** Mặc định được gắn sẵn. Khi một tool bị lỗi và Agent tự phục hồi được bằng một tool thành công ở bước sau đó, `ReflectorHook` chỉ ghi nhận bài học vào `MemoryStore` sau khi turn kết thúc sạch (`verified=True`).
- **Chạy & Mở rộng Evals:**
  1. Thêm test case vào `evals/datasets/sample.jsonl`:
     ```json
     {"id": "case-3", "input": "Check status for auth-api", "expected_substring": "auth-api"}
     ```
  2. Chạy `python3 -m evals.run_eval` để đánh giá chất lượng đồng loạt.
- **Debug bằng Trace:** Mọi bước gọi model đều được `TraceLogger` ghi ra `traces/<run_id>.jsonl` (gồm `messages`, `response`, `timestamp`, `step`) để kiểm tra và tái lập lỗi.
