# Harness AI — Kiến trúc Khung Điều phối Agent (Template & Core)

**Harness AI** là bộ khung hạ tầng (Agent Harness) chuẩn Production bằng Python, được thiết kế để tách biệt hoàn toàn **lớp điều phối bất biến** khỏi **nghiệp vụ riêng của từng dự án**.

Mục tiêu của bộ tài liệu này là làm bản thiết kế kỹ thuật (Blueprint) để khởi tạo và phát triển repo mẫu `harness-ai` theo hướng **Plug & Play**, hỗ trợ **tự học từ sai lầm (có kiểm soát)** và **tương thích trực tiếp với cấu trúc `.agent/` của Antigravity 2.0**.

---

## Chiến lược Triển khai (Lộ trình 2 Giai đoạn)

1. **Giai đoạn 1 (Hiện tại — Dự án 1 & 2): GitHub Template Repository chuẩn Package**
   - Khởi tạo từ một repo mẫu duy nhất, nhưng tách ranh giới code tuyệt đối giữa `src/harness/` (lõi hạ tầng) và `src/app/` (nghiệp vụ dự án).
   - `src/harness/` **không bao giờ** import ngược từ `src/app/`.
   - Cho phép thử nghiệm và tinh chỉnh lõi nhanh chóng mà chưa cần quy trình release package phức tạp.
2. **Giai đoạn 2 (Từ Dự án 3 trở đi): Tách `harness-core` thành Internal Python Package**
   - Khi phần lõi đã ổn định, tách `src/harness/` sang repo riêng để cài đặt qua `uv` / `pip` (`uv add git+...`).
   - Repo template lúc này chỉ còn khung vỏ (`src/app/`, `configs/`, `evals/`, `.agent/`).

---

## Mục lục Tài liệu Thiết kế

| Tài liệu | Nội dung chính |
| :--- | :--- |
| [01-architecture-overview.md](docs/01-architecture-overview.md) | Kiến trúc Hexagonal, cấu trúc thư mục repo mẫu, luồng `AgentLoop` Async & Event Streaming. |
| [02-plug-and-play-specs.md](docs/02-plug-and-play-specs.md) | Thiết kế các Protocol/Adapter (`LLMProvider`, `@tool`, `MCPBridge`, `Hooks`) và `HarnessBuilder`. |
| [03-memory-and-self-learning.md](docs/03-memory-and-self-learning.md) | Bộ nhớ ngắn hạn & dài hạn, 3 vòng lặp tự học từ sai lầm, chống *Memory Poisoning*. |
| [04-antigravity-integration.md](docs/04-antigravity-integration.md) | Tích hợp trực tiếp `.agent/` (Rules, Skills Progressive Disclosure, MCP, Hooks) của Antigravity 2.0. |
| [05-production-guardrails-and-evals.md](docs/05-production-guardrails-and-evals.md) | Circuit Breaker, Human-in-the-loop, Structured Output, Security, Trace/Replay và phạm vi **MVP v1**. |
| [06-usage-guide.md](docs/06-usage-guide.md) | Hướng dẫn cài đặt, cấu hình YAML, viết `@tool`, chạy streaming/HITL/structured output, MCP, `.agent/` và Evals. |
