# Bốn Nguyên Tắc Thiết Kế Kiến Trúc Agent Portable (Zero-Rewrite)

Tài liệu này tổng hợp các nguyên tắc cốt lõi giúp xây dựng dự án AI Agent trên **Antigravity 2.0** (giai đoạn đầu) và chuyển đổi mượt mà sang **Custom Agent Harness** (giai đoạn mở rộng) mà không phải viết lại mã nguồn hay thay đổi cấu trúc nghiệp vụ.

---

## Tổng quan kiến trúc tách lớp

Nguyên tắc xuyên suốt là tách biệt hoàn toàn **Phần Hồn (Tri thức & Công cụ)** khỏi **Phần Xác (Môi trường thực thi / IDE)**:

```text
my-fullstack-agent-project/
├── .agent/                      # [TRI THỨC] Rules, Skills, Workflows (Dùng chung 100%)
├── apps/                        # [ỨNG DỤNG] Next.js (web) & FastAPI (api) cho người dùng
├── packages/core_domain/        # [NGHIỆP VỤ] Pydantic Schemas, Business Logic, Validators
├── mcp_servers/                 # [CÔNG CỤ] Giao tiếp chuẩn qua giao thức MCP
└── harness/                     # [ĐIỀU PHỐI] Custom Agent Harness (Xây dựng ở Giai đoạn 2)
```

---

## Nguyên tắc 1: Đóng gói mọi hành động nghiệp vụ thành MCP Server ngay từ đầu

* **Vấn đề cần tránh:** Dựa dẫm vào công cụ terminal hoặc các script rời rạc chạy cục bộ trên IDE (Antigravity 2.0) để thao tác dữ liệu (ví dụ: chạy lệnh `psql` trực tiếp hoặc viết script tạm). Khi tách khỏi IDE, toàn bộ các thao tác này sẽ mất khả năng tự động hóa.
* **Giải pháp:** Ngay từ giai đoạn 1, mọi thao tác nghiệp vụ (truy vấn cơ sở dữ liệu, tính toán tài chính, gọi webhook, gửi thông báo) đều phải được đóng gói thành các **MCP Server độc lập** (sử dụng `mcp` hoặc `fastmcp` trên Python) đặt trong thư mục `mcp_servers/`.
* **Giá trị mang lại:** 
  * **Giai đoạn 1:** Khai báo MCP Server vào cấu hình của Antigravity 2.0 để sử dụng trực tiếp.
  * **Giai đoạn 2:** Custom Harness kết nối vào chính MCP Server đó qua `stdio` hoặc `SSE/HTTP` mà **không cần sửa đổi mã nguồn công cụ**.

---

## Nguyên tắc 2: Chuẩn hóa định dạng file trong `.agent/` để máy đọc được (Machine-Readable)

* **Vấn đề cần tránh:** Viết các file hướng dẫn (`.md`) trong `.agent/` theo dạng văn xuôi tự do. Con người và IDE có thể hiểu, nhưng khi xây dựng Custom Harness bằng Python, hệ thống sẽ gặp khó khăn trong việc tự động phân loại công cụ, quyền hạn và định dạng đầu ra.
* **Giải pháp:** Sử dụng chuẩn **YAML Frontmatter** ở đầu mọi file Skill và Rule để khai báo metadata có cấu trúc, kết hợp với phần nội dung Markdown bên dưới làm System Prompt.

**Ví dụ mẫu (`.agent/skills/financial-audit/SKILL.md`):**
```markdown
---
name: financial-audit
description: Đối soát giao dịch và tính toán lợi nhuận
allowed_tools:
  - db_server.query_trades
  - db_server.update_ledger
output_schema: AuditResultSchema
---

# Hướng dẫn thực thi
1. Truy xuất danh sách các giao dịch có trạng thái `PENDING`.
2. Đối chiếu số dư và tính toán chênh lệch...
```

* **Giá trị mang lại:** Khi chuyển sang Custom Harness, bạn chỉ cần một module `SkillLoader` ngắn gọn (dùng thư viện `python-frontmatter`) để tự động nạp metadata làm cấu hình gọi API LLM và nạp phần thân làm System Prompt.

---

## Nguyên tắc 3: Tách biệt Schema & Guardrails ra lớp `packages/core_domain/`

* **Vấn đề cần tránh:** Nhúng trực tiếp logic kiểm tra dữ liệu và cấu trúc JSON vào trong câu lệnh prompt hoặc viết trùng lặp giữa Backend API (`apps/api`) và công cụ AI (`mcp_servers/`).
* **Giải pháp:** Xây dựng một package Python thuần túy tại `packages/core_domain/` chứa toàn bộ **Pydantic Models** (định nghĩa Input/Output contracts) và các hàm **Validators** (luật kiểm tra tính đúng đắn mang tính tất định - Deterministic Guardrails).
* **Giá trị mang lại:** 
  * **Giai đoạn 1:** Dùng làm nguồn chân lý duy nhất (Single Source of Truth) cho cả Backend FastAPI, MCP Servers và các bài Unit Test trên Antigravity 2.0.
  * **Giai đoạn 2:** Trở thành lớp **Deterministic Guardrails** trong Custom Harness, ép kiểu đầu ra (Structured Outputs) của LLM và chặn đứng các dữ liệu sai lệch trước khi lưu xuống database.

---

## Nguyên tắc 4: Thiết kế Workflow dạng khai báo (Declarative) thay vì phụ thuộc hội thoại

* **Vấn đề cần tránh:** Vận hành các quy trình phức tạp bằng cách chat thủ công nhiều bước trên giao diện IDE. Điều này khiến logic điều phối nằm rải rác trong lịch sử chat và không thể tái sử dụng khi chạy ngầm (headless).
* **Giải pháp:** Mô tả rõ các bước xử lý, điều kiện rẽ nhánh và cơ chế xử lý lỗi thành các file cấu hình khai báo trong `.agent/workflows/` (sử dụng định dạng YAML hoặc Markdown có cấu trúc bước tường minh).
* **Giá trị mang lại:** 
  * **Giai đoạn 1:** Antigravity 2.0 đọc file workflow này để thực thi tuần tự các bước theo yêu cầu của bạn.
  * **Giai đoạn 2:** File workflow đóng vai trò là bản thiết kế kiến trúc để ánh xạ trực tiếp thành **State Machine** (máy trạng thái) trong Custom Harness mà không phải suy nghĩ lại luồng nghiệp vụ.

---

## Tóm tắt lộ trình chuyển đổi

| Thành phần | Giai đoạn 1 (Antigravity 2.0) | Giai đoạn 2 (Custom Harness) | Tỷ lệ tái sử dụng |
| :--- | :--- | :--- | :---: |
| **`.agent/` (Rules, Skills)** | Antigravity đọc trực tiếp | `SkillLoader` nạp động vào LLM | **100%** |
| **`mcp_servers/`** | Kết nối qua cấu hình IDE | Kết nối qua MCP Client trong FastAPI | **100%** |
| **`packages/core_domain/`** | Dùng cho API & Unit Test | Dùng làm Guardrails & Schema ép kiểu | **100%** |
| **Bộ điều phối (Harness)** | Do Antigravity đảm nhiệm | Tự xây dựng bằng Python trong `harness/` | *Xây mới ở GĐ 2* |