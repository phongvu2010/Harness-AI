# 04. Tích hợp Trực tiếp `.agent/` (Antigravity 2.0)

**Không cần convert sang định dạng khác.** Cấu trúc `.agent/` của Antigravity 2.0 (Markdown + YAML frontmatter + JSON) đóng vai trò là **Single Source of Truth** cho cả môi trường phát triển (IDE) và môi trường chạy độc lập (Production Harness).

---

## 1. Bảng ánh xạ `.agent/` vào Harness AI

Module `src/harness/loaders/agents_dir.py` (`AgentsDirLoader`) chịu trách nhiệm đọc thư mục `.agent/` tại root dự án và nạp vào các thành phần của Harness:

| Đường dẫn trong `.agent/` | Thành phần Harness | Cơ chế nạp & xử lý |
| :--- | :--- | :--- |
| `.agent/rules/*.md`<br>`AGENTS.md`, `GEMINI.md` | **System Instructions & Guardrails** | `RuleLoader` đọc nội dung `.md`. Rule có `always_on` được ghép thẳng vào System Prompt; rule có `trigger: model_decision` chỉ nạp khi phù hợp ngữ cảnh; chống trùng lặp theo đường dẫn file (deduplication). |
| `.agent/skills/<name>/SKILL.md` | **Procedural Memory** | Áp dụng **Progressive Disclosure** (tiết kiệm context). |
| `mcp_config.json` | **`MCPBridge` -> `ToolRegistry`** | Đọc trực tiếp JSON cấu hình `mcpServers` để kết nối các MCP server (`stdio` hoặc `SSE/HTTP`). |
| `hooks.json` | **Lifecycle Middleware** | Gắn các script/command vào các sự kiện (`before_tool_call`, `after_tool_call`...) trong `AgentLoop`. |

---

## 2. Cơ chế Progressive Disclosure cho `SKILL.md`

Để không làm tràn Context Window khi dự án có hàng chục Skills:
1. **Lúc khởi động (`Context Assembly`):** `AgentsDirLoader` chỉ parse phần **YAML frontmatter** (`name`, `description`) của các file `.agent/skills/*/SKILL.md` và chèn danh sách tóm tắt này vào System Prompt.
2. **Lúc thực thi:** `ToolRegistry` cung cấp sẵn một built-in tool an toàn (ví dụ `read_skill(skill_name)` hoặc `read_file` bị giới hạn trong `.agent/skills/`).
3. Khi Agent nhận thấy tác vụ khớp với `description` của một skill nào đó, nó tự gọi tool để đọc toàn bộ nội dung `SKILL.md` (và các file trong `scripts/`, `references/` nếu cần).

---

## 3. Lưu ý khi dùng chung cho IDE và Production Runtime

1. **Phân tách Skill Dev vs. Runtime:** Các skill viết riêng cho IDE (ví dụ nhắc đến tool trình duyệt hoặc sidecar của Antigravity) nên được đánh dấu hoặc đặt điều kiện để `AgentsDirLoader` trên Production chỉ nạp các skill phục vụ nghiệp vụ app.
2. **Tương thích với cơ chế Tự học:** Khi *Optimizer Agent* (tài liệu 03) rút kinh nghiệm và đề xuất cập nhật quy trình, nó ghi trực tiếp vào `.agent/skills/` hoặc `.agent/rules/` dưới dạng Markdown -> dễ đọc, dễ kiểm soát qua Git PR.
