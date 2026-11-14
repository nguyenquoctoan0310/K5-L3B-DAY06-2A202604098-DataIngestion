# Báo cáo cá nhân — Day 6

> **Trạng thái nhãn:** cả **80 nhãn audit do tác giả tự chấm** (coherence 20, grounding 15, metadata 45). LLM (Claude Opus 5.5) chấm nháp trước theo thang 0–2 của GUIDE §4, sau đó tác giả duyệt trên giao diện review local (`python -m tools.review_app`) và đối chiếu với trang PDF gốc:
> - **Coherence và grounding: chấm mù.** Tác giả nhập nhãn của mình trước, sau đó mới thấy nhãn AI. Coherence đồng ý 15/20 (75%), grounding đồng ý 15/15. Nhãn cuối cùng là nhãn của tác giả ([review_log.json](../../starter/results/review_log.json)).
> - **Metadata: tác giả kiểm tra từng dòng** với trang PDF đã render, chunk được tô vàng trên trang. Kết quả 45/45 đúng, khớp với kiểm tra tự động.
>
> Mọi số liệu lấy từ file trong `starter/results/` của lần chạy ngày 2026-11-14; không có số nào điền tay.

## Dữ liệu và pipeline
- **Chủ đề, nguồn/quyền, số trang và quy tắc tính trang:**
  - Sách OpenStax *Introduction to Behavioral Neuroscience* (©2024 Rice University), giấy phép **CC BY-NC-SA 4.0** (ghi ở PDF tr. 4); dùng phi thương mại, có ghi nguồn. Snapshot: PDF 913 trang, ngày tạo 2026-06-09, có sha256 trong [data/manifest.json](../../data/manifest.json). File PDF không commit (250 MB); tải lại theo link trong manifest.
  - Corpus gồm **80 trang nội dung, 190.904 ký tự**: Ch.6 *Vision* (PDF tr. 251–294, 44 trang, 97.092 ký tự) và Ch.7 *Hearing and Balance* (tr. 303–338, 36 trang, 93.834 ký tự).
  - Quy tắc tính trang: tính trang vật lý thuộc phần trình bày của chương (trang mở đầu chương và các mục đánh số). Loại: Section Summary, Key Terms, References, Multiple Choice, Fill in the Blank. `page` là số trang vật lý của PDF; `printed_page` là số in trên trang (ví dụ PDF tr. 252 in số 240).
  - Lý do chọn 2 chương: cùng là giác quan, dùng chung khái niệm (receptor, ganglion, thalamus, cortex). Nhờ vậy câu hỏi không nêu tên giác quan sẽ có đáp án nghe hợp lý ở cả hai chương, và filter có tác dụng thật.
- **Chuẩn hóa, lỗi và giới hạn:**
  - Đọc PDF bằng PyMuPDF 2.1 (thay cho pypdf, vì pypdf không giữ ranh giới đoạn).
  - Nhận dạng heading **theo từng dòng** dựa vào cỡ chữ (13.0 là mục, 10.8 là tiểu mục).
  - Bỏ header/footer theo vị trí (y < 45 hoặc y > 745), kèm regex dự phòng: 78 dòng số trang/tên chương và 40 dòng "Access for free at openstax.org". Bỏ thêm 2 dòng link tác giả/đa phương tiện.
  - Chuẩn hóa NFKC, đổi ngoặc cong thành ngoặc thẳng, bỏ soft hyphen. Gạch nối cuối dòng được giữ: đã kiểm tra cả 15 trường hợp, đều là từ ghép (*on-center*, *high-acuity*).
  - Không có trang nào cần OCR. Log có 2 cảnh báo: tr. 285 chỉ có 69 ký tự (gần như toàn hình) và tr. 338 có 291 ký tự (cuối mục) ([ingest_log.json](../../starter/results/ingest_log.json)).
  - Lỗi đã phát hiện và sửa: (1) PyMuPDF gộp tiêu đề phụ với đoạn văn trong một block, làm 1.313 ký tự thân bài bị coi là heading và chunk B dài 1.396 ký tự; sửa bằng cách phân loại theo dòng. (2) Dòng "CHAPTER 6" bị lặp so với tiêu đề chương.
  - Giới hạn: URL ghi công ảnh trong chú thích hình được giữ lại vì là nội dung gốc.
- **Embedding, vector store, metric, lệnh chạy và chi phí:**
  - Embedding: `intfloat/multilingual-e5-base`, chạy local bằng sentence-transformers 6.3.0 (transformers 5.23.0, torch 2.15.0 bản CPU), revision `d128750597153bb5987e10b1c3493a34e5a4502a`, **768 chiều**. Tiền tố `query: ` cho câu hỏi và `passage: ` cho chunk; chuẩn hóa L2; giới hạn 512 token, không chunk nào bị cắt (`truncated_documents = 0`).
  - Vector store: PostgreSQL 16.16 + pgvector 0.8.7 (Docker), bảng `chunks`, metric **cosine** (`<=>`), **tìm kiếm chính xác** (không dùng index ANN).
  - Python 3.11.0. Lệnh chạy: xem mục [Cách chạy lại](#cách-chạy-lại) ở cuối báo cáo.
  - **Chi phí: 0 USD** (model local, CPU).
  - Môi trường thử nghiệm ban đầu với Gemini đã bỏ; không có dữ liệu benchmark nào được gửi qua API.
- **Metadata bắt buộc, trường lọc bổ sung, nguồn giá trị và lý do chọn:**
  - Bắt buộc: `document_id` (`openstax-ibn`), `source` (tên file PDF), `page` (trang vật lý).
  - Bổ sung: `printed_page` (int, đọc từ footer), `chapter` (text, lấy từ phạm vi trang khai báo theo mục lục PDF), `chapter_title`, `section` (text, mục đang mở tại vị trí bắt đầu chunk, xác định từ heading cỡ 13.0), `char_start/char_end` (vị trí trong trang, để truy vết), `content_hash` (phát hiện thay đổi khi nạp lại).
  - Trường lọc: **`chapter`**. Giá trị lấy từ cấu trúc sách, không phải đoán, và phân biệt rõ 2 phạm vi. Store chỉ cho phép lọc trên `chapter` và `section` (whitelist).

## Cấu hình và benchmark
- **A / B / C:** ([src/config.py](../../starter/src/config.py), [experiment_log.csv](../../artifacts/experiment_log.csv)). Cả 3 dùng cùng corpus, model, store, metric cosine và top-3.
  - **A:** cửa sổ cố định **500 ký tự, overlap 50**, cắt riêng từng trang, không quan tâm cấu trúc. Kết quả 454 chunk.
  - **B:** gom đoạn theo heading/đoạn văn, **max 800 ký tự thân bài** (heading gắn liền đoạn sau, tối đa ~100 ký tự thêm), **min 150** (chunk ngắn hơn ghép vào chunk trước), đoạn dài cắt theo câu. Kết quả 333 chunk.
  - **C:** **dùng lại đúng index B**; khác biệt duy nhất so với B là filter `chapter`, chỉ áp dụng cho câu hỏi có filter. Các câu khác C chạy như B.
- **Benchmark:** 22 câu, **16 dev / 6 test**, chia trước khi chạy ([benchmark/README.md](../../benchmark/README.md)).
  - Theo loại câu: 9 trực tiếp, 6 diễn đạt lại (tiếng Việt hỏi trên tài liệu tiếng Anh), 4 điều kiện/phân biệt, 3 cần metadata filter (q15, q16 thuộc dev; t21 thuộc test).
  - Câu có filter được viết không nêu tên giác quan, để cả hai chương đều có đáp án nghe hợp lý. Filter là ngữ cảnh giả định ("người dùng đang học chương X"), không lấy từ gold.
  - Kiểm chứng: [verify_benchmark.py](../../starter/scripts/verify_benchmark.py) xác nhận 22/22 `evidence_quote` nằm trên `gold_page` sau chuẩn hóa, không câu nào trùng ở trang khác, và filter khớp metadata của trang gold.
  - Chia dev/test theo trang (không có gold page chung); theo mục thì chỉ chia được một phần (rủi ro rò rỉ ở mục 7.2 và 6.5 đã ghi lại).
- **Tiêu chí chọn chiến lược đã chốt trước khi xem test:** trên dev, xét Evidence Hit@3, nếu hòa thì xét Hit@1, sau đó coherence, rồi số chunk. Code buộc chạy dev và ghi [selection.json](../../starter/results/selection.json) trước khi cho phép chạy test.

## Kết quả
| Strategy | Split | Hit@1 (n/N) | Evidence Hit@3 (n/N) | Chunk count | Thời gian | Chi phí |
|---|---|---|---|---|---|---|
| A | dev | 8/16 | **11/16** | 454 | ingest 67,7 s (embed 66,3 s); query ~84 ms | 0 |
| B | dev | 7/16 | 9/16 | 333 | ingest 65,1 s (embed 64,1 s); query ~108 ms | 0 |
| C | dev | 7/16 | 10/16 | 333 (index B) | dùng index B; query ~86 ms | 0 |
| A | test | 3/6 | 4/6 | 454 | như trên | 0 |
| B | test | 4/6 | **6/6** | 333 | như trên | 0 |
| C | test | 4/6 | **6/6** | 333 (index B) | như trên | 0 |

Đọc PDF và chuẩn hóa mất 8,1 s. Nạp lại cùng phiên bản: 787/787 chunk không đổi, 0 chunk embed lại, khoảng 0,1 s mỗi index, số dòng trong DB bằng số chunk ([ingest_run.json](../../starter/results/ingest_run.json)). Thời gian query là một lần chạy trên CPU, chỉ để tham khảo.

**Thứ tự chạy và holdout:** chọn chiến lược trên dev lúc 04:12:58 UTC ([selection.json](../../starter/results/selection.json)); chạy test **một lần** lúc 04:13:39 UTC. Sau đó không đổi tham số, câu hỏi hay gold. Có một lần **tính lại chỉ số từ trace đã lưu** (`python -m src.run metrics`, không chạy lại retrieval) để sửa *cách đếm* tuân thủ filter cho đúng GUIDE §4.3: ban đầu chỉ đếm cho C, sau khi sửa thì đếm trên mọi câu yêu cầu filter cho cả A, B, C. Hit@1 và Hit@3 không đổi.

| Strategy | Coherence trung bình /2 (n) | Metadata đủ và đúng (n/N) | Filter tuân thủ (n/N hoặc N/A) | Grounding 0/1/2 (số câu) và trung bình /2 |
|---|---|---|---|---|
| A | 1,0 (10): 0×0, 10×1, 0×2 | 15/15 | dev 0/2, test 1/1 | 0 / 1 / 4, trung bình **1,8** (5) |
| B | 1,6 (10): 0×0, 4×1, 6×2 | 15/15 | dev 0/2, test 0/1 | 0 / 0 / 5, trung bình **2,0** (5) |
| C | 1,6 (10; cùng chunk với B) | 15/15 | dev **2/2**, test **1/1** | 0 / 0 / 5, trung bình **2,0** (5) |

Tất cả nhãn trong bảng là nhãn của tác giả. Nếu dùng bản nháp LLM thì coherence là A 1,0 và B 1,9; grounding và metadata không đổi. Hai cách đều cho kết luận A < B.

**Đường dẫn trace, nhãn thủ công và cách chọn mẫu:**
- Trace top-3 đầy đủ (câu hỏi, filter, chunk id/text/score/metadata, cờ hit): `starter/results/traces_{A,B,C}_{dev,test}.jsonl`. Chỉ số: `metrics_dev.json`, `metrics_test.json`.
- Nhãn: [audit_coherence.csv](../../starter/results/audit_coherence.csv), [audit_metadata.csv](../../starter/results/audit_metadata.csv), [audit_grounding.csv](../../starter/results/audit_grounding.csv); tổng hợp ở `audit_summary.json` (tạo bởi `make_audit.py` và `summarize_audit.py`).
- Cách chọn mẫu, cố định trước khi chấm:
  - Coherence: điểm giữa văn bản của 10 trang nội dung đầu (PDF tr. 251–260), chấm chunk A và chunk B chứa vị trí đó. C dùng chunk của B nên cùng nhãn.
  - Metadata và grounding: 5 câu test đầu theo ID (t17–t21) × top-3 × 3 cấu hình.
- Kiểm tra metadata: tự động đối chiếu với PDF gốc (đoạn text của chunk phải có trên trang PDF ghi trong metadata; chương phải đúng phạm vi trang; section phải có trên trang đó). Sau đó tác giả duyệt tay cả 45 dòng trên trang PDF đã render.
- Tuân thủ filter được tính trên **mọi câu yêu cầu filter, cho cả 3 cấu hình** (A và B không áp filter).

## Phân tích và kết luận
- **So sánh A–B (chỉ đổi cách chunk):**
  - Retrieval **không phân thắng bại một cách đáng tin**: A thắng trên dev (11/16 so với 9/16), B thắng trên test (6/6 so với 4/6). Gộp 22 câu thì **cả hai đều 15/22 Hit@3 và 11/22 Hit@1**. Khác biệt chỉ là 2–3 câu, nằm trong mức dao động khi mẫu nhỏ.
  - Theo loại câu, A trội ở câu điều kiện (4/4 so với 1/4), B trội ở câu diễn đạt lại (5/6 so với 3/6). Mỗi nhóm chỉ có 4–6 câu nên chỉ là gợi ý.
  - Chất lượng ngữ cảnh khác biệt rõ: coherence 1,0 so với 1,6. Cả 10 chunk A đều bắt đầu hoặc kết thúc giữa từ/câu ("hest visual acuity", "Sharp vision requ") nên đều bị chấm 1. B không hoàn hảo: 4/10 chunk B bị chấm 1 (p252, p257, p258, p260). Các chunk này kết thúc trọn câu, nhưng tác giả đánh giá chúng thiếu ngữ cảnh của đoạn liền kề (lý do ghi "thiếu", "câu cuối thiếu"). Grounding 1,8 so với 2,0.
  - Về tài nguyên, B ít hơn 27% số chunk (333 so với 454) và ít hơn 9% tổng ký tự, vì A lặp phần overlap.
  - **Lưu ý về biến gây nhiễu:** A và B khác cả *phương pháp* lẫn *độ dài trung bình* (462 so với 572 ký tự), nên chưa tách được hai yếu tố này.
- **So sánh B–C (chỉ đổi filter):** filter chỉ thay đổi kết quả ở các câu có filter.
  - q15 từ trượt thành trúng (chunk gold từ hạng 6 lên hạng 3).
  - q16 vẫn trượt nhưng chunk gold lên hạng 5 (trước đó hạng 10).
  - t21 trúng ở cả B và C, nhưng **C loại được chunk LGN của chương 6** khỏi top-3. B có chunk này ở hạng 2, tức là ngữ cảnh lẫn chương sai.
  - Tuân thủ phạm vi: C 3/3; A và B không filter lần lượt 1/3 và 0/3.
  - Hạn chế: filter chỉ có tác dụng khi người dùng cung cấp đúng chương; với q16, filter đúng vẫn chưa đủ để đưa chunk gold vào top-3.
- **Ví dụ lỗi 1 (A, t21, chunk cắt ngang câu trích):** câu trích về MGB nằm ở vị trí ký tự 821–966 của PDF tr. 321. Chunk A `p321:f01` kết thúc ở ký tự 950, còn `f02` bắt đầu từ 900, nên không chunk nào chứa trọn câu. Cả 3 kết quả top-3 đều đúng trang 321 nhưng Evidence Hit = 0. Người chấm vẫn cho grounding 2, vì top-1 đủ để trả lời. Điều này cho thấy so khớp chuỗi chặt có thể **đánh giá thấp** A.
- **Ví dụ lỗi 2 (B, q13, đúng trang nhưng sai chunk):** câu về *sensorineural hearing loss*, gold là PDF tr. 317. Cả top-3 của B đều thuộc tr. 317, nhưng chunk chứa bằng chứng (849 ký tự, mở đầu bằng box "DEVELOPMENTAL PERSPECTIVE…") chỉ đứng hạng 4. A tìm thấy ở hạng 2. Chunk dài gộp nhiều ý (thống kê, nguyên nhân, phân loại) nên vector bị "pha loãng". Ở q04 và q09, B cũng có chunk gold ở hạng 5–6.
- **Chiến lược chọn, đánh đổi, điều kiện áp dụng:**
  - Theo tiêu chí đã chốt, **A được chọn** (dev Hit@3 11/16). Mình **không đổi lựa chọn sau khi xem test**.
  - Tuy vậy, bằng chứng tổng thể không ủng hộ việc A tốt hơn: test, coherence, grounding và tài nguyên đều nghiêng về B/C, và gộp 22 câu thì retrieval hòa.
  - Nhận định có điều kiện, chỉ áp dụng cho corpus này: nếu chunk được đưa vào LLM để trả lời, **C** phù hợp hơn khi người dùng cung cấp được phạm vi chương, vì ngữ cảnh trọn ý và không lẫn chương. A chỉ có lợi khi câu trích rất ngắn và cần chunk nhỏ, tập trung.
  - Bài học về phương pháp: tiêu chí chỉ dựa trên Hit@3 của 16 câu là quá nhạy với 2 câu. Lần sau nên chốt trước một ngưỡng "khác biệt tối thiểu" (ví dụ ≥ 3 câu) rồi mới xét đến coherence/grounding.
- **Hạn chế:**
  - Test chỉ có 6 câu, trong đó 5 câu thuộc chương 7; mỗi câu tương đương 16,7 điểm %.
  - Câu trích ngắn (trung bình ~115 ký tự, 1 câu) có lợi cho chunk nhỏ.
  - Hit dùng so khớp chuỗi chặt (xem t21). Mỗi câu chỉ có một gold page.
  - Nhãn: chỉ có **một người chấm** (tác giả), cỡ mẫu nhỏ (coherence 20, grounding 15). Đồng thuận người–LLM ở coherence là 15/20. Trong 5 chỗ lệch, có 4 chỗ LLM *dễ dãi hơn* với chunk B (LLM chấm 2, tác giả chấm 1). Điều này cho thấy nếu chỉ dùng LLM chấm sẽ đánh giá B cao hơn thực tế (1,9 so với 1,6).
  - Grounding đồng ý 15/15. Tác giả không ghi lý do riêng khi đồng ý, nên cột `reason` của grounding vẫn là lý do tiếng Anh của LLM. Nhãn LLM được hiển thị sau khi tác giả nhập nhãn, nên có thể vẫn ảnh hưởng đến các dòng chấm sau. Câu hỏi benchmark do AI soạn nháp.
  - **t21 đã bị "nhìn thấy" một phần** khi thử store (đã ghi trong benchmark README).
  - Không chunk vắt qua 2 trang, nên câu có thể bị cắt ở ranh giới trang (B tại tr. 255: LLM chấm 1 vì lý do này, tác giả chấm 2).
  - Thời gian đo một lần trên một máy CPU.
- **Nếu làm lại hoặc mở rộng:**
  1. Thêm cấu hình B' với max 500 để tách ảnh hưởng của *phương pháp* khỏi *kích thước chunk*.
  2. Cho chunk nối tiếp qua ranh giới trang, lưu `page_start/page_end`.
  3. Tăng benchmark lên khoảng 50 câu, dùng câu trích nhiều câu, cân bằng hai chương trong test, có người chấm thứ hai.
  4. Thử hybrid BM25 + dense (giữ nguyên chunk và filter) cho câu điều kiện có từ khóa đặc thù.
  5. Thử filter theo `section` cho câu hỏi hẹp hơn.

## Cách chạy lại
Yêu cầu: Docker, Python 3.11, khoảng 1,2 GB cho model local; không cần API key. Chạy trong thư mục `starter/` (PowerShell):

```powershell
python -m venv .venv; .venv\Scripts\Activate.ps1
python -m pip install torch==2.15.0 --index-url https://download.pytorch.org/whl/cpu
python -m pip install -r requirements.txt
Copy-Item .env.example .env              # đặt EMBEDDING_MODEL=intfloat/multilingual-e5-base
# Tải PDF theo link trong data/manifest.json, lưu vào
# starter/data/Introduction-Behavioral-Neuroscience_-_WEB.pdf (đối chiếu sha256 trong manifest)
docker compose -p day6_student_starter up -d --wait
python scripts/check_setup.py

python -m scripts.build_manifest         # đọc + chuẩn hóa -> data/manifest.json, results/ingest_log.json
python -m scripts.verify_benchmark       # kiểm chứng 22 evidence trên gold page
python -m scripts.chunk_stats            # thống kê chunk A/B -> results/chunk_stats.json
python -m src.run ingest                 # chunk + embed + upsert A/B vào pgvector (chạy lại không nhân đôi)
python -m src.run evaluate --split dev   # A/B/C trên dev -> traces, metrics_dev.json, selection.json
python -m src.run evaluate --split test  # chỉ chạy được sau khi đã có selection.json
python -m scripts.make_audit             # tạo bảng audit (giữ nguyên nhãn đã có)
python -m scripts.summarize_audit        # tổng hợp nhãn -> results/audit_summary.json
```

Nếu gặp `UnicodeEncodeError` khi in ra console Windows, đặt `$env:PYTHONIOENCODING="utf-8"`. Khi model đã có trong cache, có thể đặt `$env:HF_HUB_OFFLINE="1"`.

## Sử dụng AI
- Công cụ: Claude Code (Claude Opus 5.5). AI viết code pipeline, soạn nháp 22 câu benchmark từ text đã trích, và **chấm nháp 80 nhãn audit với vai trò LLM judge**: đọc toàn văn từng chunk hoặc bộ top-3, áp thang 0–2 của GUIDE §4, ghi lý do từng dòng. Sau đó tác giả tự chấm lại toàn bộ (xem ghi chú trạng thái nhãn ở đầu báo cáo). Cột `labeled_by` ghi người chấm và cách chấm của từng dòng ("blind review, agrees/changed AI draft …" hoặc "web review, checked against rendered PDF page"); nhãn gốc của AI được giữ trong cột `ai_label`.
- Cách kiểm tra:
  - Script kiểm chứng evidence; kiểm tra metadata tự động đối chiếu PDF gốc.
  - Kiểm thử nạp lại, kiểm thử tài liệu thay đổi, kiểm thử chặn filter không hợp lệ.
  - Đếm ký tự để xác nhận chunk B không làm mất văn bản (160.309 = 160.309).
  - Rà lại một lượt các nhãn ở ranh giới giữa hai mức điểm (p251, p252, p255).
  - Tác giả chấm mù 35 nhãn coherence/grounding và duyệt 45 nhãn metadata trên giao diện review (`python -m tools.review_app`). Kết quả: coherence đồng ý 15/20, grounding 15/15, metadata 45/45 đúng ([review_log.json](../../starter/results/review_log.json)). Tổng hợp lại bằng `python -m scripts.summarize_audit`.
