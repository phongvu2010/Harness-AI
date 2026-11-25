# 05. Guardrails Production, Evals & Phạm vi MVP v1

Tài liệu này tổng hợp 5 yêu cầu bắt buộc khi đưa Agent ra Production và chốt phạm vi triển khai cho **phiên bản v1** để tránh Over-engineering.

---

## 1. Năm Trụ cột Production

### 1.1. Circuit Breaker & Budget Guard (Kiểm soát vòng lặp & Chi phí)
- **`max_steps`:** Giới hạn số vòng suy luận tối đa trong 1 lượt chạy (mặc định `15`).
- **`max_tokens` / `max_cost_usd`:** Ngắt mạch ngay khi tổng token hoặc chi phí phiên vượt ngưỡng.
- **`timeout_seconds`:** Timeout cho từng lần gọi Model và từng lần thực thi Tool.
- **Loop Detector:** Theo dõi lịch sử `(tool_name, args_hash)`; nếu Agent gọi lặp lại giống hệt nhau quá `2–3` lần liên tiếp -> ép dừng hoặc tiêm cảnh báo đổi hướng.

### 1.2. Human-in-the-loop (Pause & Resume State)
- Các tool có tác dụng phụ (ghi/xóa DB, gửi email, gọi API thanh toán) gắn cờ `requires_approval=True`.
- Khi gặp tool này: `AgentLoop` lưu toàn bộ `SessionState` (bao gồm `pending_tool_call`), phát sự kiện `ApprovalRequired`, và dừng chờ.
- Khi người dùng xác nhận (`approve=True/False`), hệ thống nạp lại `SessionState` và chạy tiếp từ đúng điểm dừng.

### 1.3. Structured Output & Auto-Repair
- Hỗ trợ truyền `response_schema: type[BaseModel]` khi dùng AI như một service backend.
- Nếu đầu ra không hợp lệ theo Pydantic, Harness tự bắt `ValidationError` và gửi ngược thông báo lỗi cho model tự sửa (tối đa `2` lần retry) trước khi ném exception.

### 1.4. Security & Sandboxing
- **Secret Isolation:** Tuyệt đối không đưa API Key, DB Password vào System Prompt hoặc Tool Output.
- **Least Privilege:** Tool đọc/ghi file phải bị khóa trong thư mục sandbox cho phép; tool SQL chỉ cấp quyền `SELECT` (read-only) trừ khi có quy định riêng.
- **Input/Output Guardrails:** Hỗ trợ gắn hook lọc PII hoặc kiểm tra Prompt Injection trước khi gọi tool nhạy cảm.

### 1.5. Observability, Trace & Replay
- Mỗi phiên chạy có `run_id` duy nhất.
- Ghi nhận **Trace JSONL** (lưu System Prompt, Messages, Tool Specs, Model Config, Raw Responses, Token Usage, Latency).
- Cho phép **Replay** lại đúng ca lỗi trên môi trường local để debug và chuyển thẳng ca lỗi đó thành test case trong `evals/datasets/`.

---

## 2. Khung Đánh giá Chất lượng (`evals/`)

Mọi dự án sinh ra từ template đều có sẵn quy trình Eval-Driven:
- `evals/datasets/*.jsonl`: Chứa các cặp `(input, expected_behavior_or_output, tags)`.
- `evals/scorers.py`:
  - *Deterministic Scorers:* Kiểm tra JSON schema, kiểm tra đúng tool được gọi, exact/regex match.
  - *LLM-as-a-Judge:* Dùng model chấm điểm tiêu chí ngữ nghĩa (độ chính xác, tính an toàn, tuân thủ rule).
- `evals/run_eval.py`: Chạy toàn bộ dataset qua `AgentLoop`, xuất báo cáo tỷ lệ Pass, độ trễ trung bình và tổng chi phí.

---

## 3. Phạm vi Chốt cho MVP v1 (Khi bắt tay vào Code)

Để bộ repo mẫu gọn nhẹ, dễ hiểu và chạy được ngay từ ngày đầu, **v1** chỉ cần triển khai đúng các hạng mục sau:

1. **Project Setup:** `pyproject.toml` dùng `uv`, Python `>=3.11`, `Pydantic v2`, tách rõ `src/harness/` và `src/app/`.
2. **Core Loop:** `AgentLoop` (`async def`, `Event Stream`), `SessionState` (hỗ trợ Pause/Resume), `CircuitBreaker` (`max_steps`, `max_tokens`, loop detector).
3. **Plug & Play Adapters:**
   - `LLMProvider` Protocol + 1 adapter mẫu (`GeminiAdapter` dùng `google-genai` hoặc `LiteLLMAdapter`).
   - `@tool` decorator (auto JSON Schema + `requires_approval`) + `ToolRegistry`.
   - `MCPBridge` kết nối MCP Server cơ bản.
   - `AgentsDirLoader` đọc `.agent/rules/*.md`, `.agent/skills/*/SKILL.md` (Progressive Disclosure), và `mcp_config.json`.
4. **Memory & Observability (Cơ bản):**
   - `ShortTermMemory` (sliding window + compaction cơ bản).
   - `MemoryStore` Protocol + `InMemoryStore` mặc định (làm chỗ cắm cho `EpisodicMemory` sau này).
   - `JsonlTraceLogger` ghi log từng `run_id`.
5. **Testing & Evals:**
   - `tests/` dùng `MockLLMProvider` test toàn bộ core loop, circuit breaker, pause/resume (không tốn API key).
   - `evals/` với 1 dataset mẫu + 1 runner chạy được ngay.
