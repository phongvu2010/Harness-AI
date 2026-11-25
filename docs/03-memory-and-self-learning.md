# 03. Bộ nhớ Ngắn hạn, Dài hạn & Cơ chế Tự học từ Sai lầm

Hệ thống AI Harness không tự động fine-tune trọng số LLM theo thời gian thực (quá đắt, chậm và dễ gây *catastrophic forgetting*). Thay vào đó, việc quản lý trí nhớ và "tự học" được thực hiện ở **tầng Ngữ cảnh & Quy trình (In-Context Learning & Harness Self-Evolution)**.

---

## 1. Kiến trúc Bộ nhớ 2 Tầng (`src/harness/memory/`)

### A. Bộ nhớ ngắn hạn (Short-term / Working Memory)
Quản lý lịch sử hội thoại, kết quả tool calls và trạng thái trong **phiên làm việc hiện tại**.
- **Sliding Window & Compaction:** Khi lịch sử vượt ngưỡng cấu hình (ví dụ 75% context window), `ContextManager` giữ nguyên vài lượt gần nhất và dùng LLM tóm tắt các lượt cũ thành một block `SummaryMessage`.
- **Output Offloading:** Khi một tool trả về dữ liệu quá lớn (ví dụ log 5.000 dòng), Harness lưu toàn bộ ra file tạm/artifact, chỉ đưa phần tóm tắt + đường dẫn file vào Short-term memory để tránh làm loãng sự tập trung của model.

### B. Bộ nhớ dài hạn (Long-term / Persistent Memory)
Lưu trữ xuyên suốt các phiên làm việc, chia làm 3 loại:
1. **Semantic Memory (Tri thức & Sự thật):** Tài liệu nghiệp vụ, quy định, hồ sơ người dùng (truy xuất qua RAG / Vector DB / SQL).
2. **Episodic Memory (Ký ức kinh nghiệm):** Lưu các *"ca"* đã chạy trong quá khứ dưới dạng: `Ngữ cảnh/Triệu chứng -> Hành động đã thử -> Kết quả (Thành công/Thất bại) -> Bài học rút ra`.
3. **Procedural Memory (Kỹ năng & Quy trình):** Các file hướng dẫn chuẩn (`.agent/skills/*/SKILL.md`, `.agent/rules/*.md`, System Prompts).

---

## 2. Cơ chế Tự học từ Sai lầm (3 Vòng lặp Phản hồi)

### Cấp độ 1: Reflection + Episodic Memory (Tức thì ở cấp độ Tác vụ)
- **Khi có lỗi:** Hook `on_error` kích hoạt một bước **Reflector** phân tích nguyên nhân gốc rễ và rút ra 1 bài học ngắn gọn.
- **Tái sử dụng:** Ở các lượt chạy sau, khi nhận yêu cầu có ngữ cảnh tương tự, Harness truy xuất top-K bài học liên quan từ `EpisodicMemory` và tiêm (inject) vào System Prompt trước khi Agent hành động.

### Cấp độ 2: Procedural & Eval Evolution (Cấp độ Hệ thống — Offline)
- Định kỳ, một **Optimizer Agent** đọc log lỗi từ `observability/` và phản hồi của người dùng.
- Nó tự động đề xuất:
  1. Cập nhật/bổ sung **Skill** hoặc **Rule** trong `.agent/`.
  2. Viết lại **Tool Description** cho rõ ràng hơn (giảm lỗi gọi nhầm tham số).
  3. Sinh thêm **Regression Test Case** mới vào `evals/datasets/`.

### Cấp độ 3: Golden Dataset -> Fine-tuning (Dài hạn)
- Các ca sai lầm đã được con người sửa chuẩn được gom vào `evals/datasets/golden.jsonl`. Khi đủ lớn, dữ liệu này dùng để benchmark hoặc fine-tune model nhỏ hơn nhằm giảm chi phí.

---

## 3. Quy tắc An toàn: Chống Ngộ độc Bộ nhớ (Memory Poisoning)

> **Rủi ro:** Nếu cho AI tự do lưu mọi suy diễn sai lầm vào bộ nhớ dài hạn, hệ thống sẽ tự khuếch đại lỗi sai đó mãi mãi.

**3 cơ chế bảo vệ bắt buộc:**
1. **Verified-only Episodic Learning:** Chỉ lưu bài học vào `EpisodicMemory` khi có tín hiệu xác thực khách quan (lần thử lại sau đó thành công, unit test pass, hoặc người dùng xác nhận).
2. **Human-in-the-loop cho Procedural Memory:** Mọi thay đổi do *Optimizer Agent* đề xuất vào `.agent/skills/`, `.agent/rules/` hay code đều phải đi qua **Git Pull Request** + chạy **Eval Harness** chứng minh điểm số tăng mới được merge.
3. **Decay & Pruning:** Gắn `confidence_score` và timestamp cho ký ức; tự động hạ ưu tiên hoặc xóa ký ức lỗi thời khi nghiệp vụ/API thay đổi.

---

## 4. Interface chuẩn hóa (`MemoryStore`)

```python
from typing import Protocol

class MemoryStore(Protocol):
    async def add_episode(self, episode: EpisodeRecord) -> None: ...
    async def search_episodes(self, query: str, limit: int = 3) -> list[EpisodeRecord]: ...
    async def save_state(self, session_id: str, state: SessionState) -> None: ...
    async def load_state(self, session_id: str) -> SessionState | None: ...
```

- **Dev:** Dùng `InMemoryStore` hoặc `SQLite/JSONL`.
- **Prod:** Cắm `Redis` (cho `SessionState`) + `pgvector`/`Qdrant` (cho `Episodic/Semantic Memory`).
