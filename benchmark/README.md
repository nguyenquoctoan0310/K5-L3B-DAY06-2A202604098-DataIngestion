# Benchmark — 22 câu (16 dev / 6 test)

File: [questions.jsonl](questions.jsonl). Kiểm chứng: `cd starter && python -m scripts.verify_benchmark`.

**Chốt ngày 2026-11-14, trước khi chạy bất kỳ retrieval nào.** Không chỉnh câu hỏi, gold hay split sau khi xem kết quả; nếu buộc phải sửa, ghi lý do vào mục *Thay đổi* bên dưới.

## Trường dữ liệu
`id, split, type, question, expected_answer, gold_source_id, gold_page, evidence_quote, filter` (+ `filter_rationale` cho câu có filter).
- `gold_page` = số trang **vật lý** trong PDF (không phải số in trên trang), khớp trường `page` của chunk.
- `evidence_quote` trích nguyên văn từ text đã chuẩn hóa; so khớp bằng `normalize_for_match` (NFKC, gộp khoảng trắng, chữ thường).
- `type`: `truc_tiep` (hỏi thẳng, cùng từ vựng), `dien_dat_lai` (diễn đạt khác, phần lớn bằng tiếng Việt, hỏi trên tài liệu tiếng Anh), `dieu_kien` (cần phân biệt hai trường hợp/điều kiện), `metadata` (cần filter).

## Phân bố
| | dev | test |
|---|---:|---:|
| truc_tiep | 6 | 3 |
| dien_dat_lai | 5 | 1 |
| dieu_kien | 3 | 1 |
| metadata (filter `chapter`) | 2 | 1 |
| **Tổng** | **16** | **6** |
| Chương 6 / Chương 7 (theo gold) | 9 / 7 | 1 / 5 |

## Câu có filter
Filter `chapter` là **ngữ cảnh giả định được cung cấp trước khi truy xuất** (người dùng đang học chương đó), không suy ra từ gold. Câu hỏi được viết **không nêu tên giác quan**, để cả hai chương đều có câu trả lời nghe hợp lý:
- q15 (ch.6): dây thần kinh rời cơ quan cảm giác → optic nerve; đối thủ ở ch.7: auditory nerve.
- q16 (ch.7): thân tế bào của neuron dẫn tín hiệu vào não → spiral ganglion; đối thủ ở ch.6: bipolar/ganglion cells.
- t21 (ch.7): nhân đồi thị là cửa ngõ lên vỏ não → medial geniculate body; đối thủ ở ch.6: LGN (p.272).

Cấu hình A và B chạy các câu này **không filter**; C áp dụng filter. Câu không có filter thì C chạy như B.

## Cách chia dev/test và rủi ro rò rỉ
- Chia **theo trang**: không câu test nào có gold page trùng với gold page của câu dev (trang test: 289, 316, 321, 323, 334, 337).
- Chia **theo mục** chỉ làm được một phần: mục 7.4 (tiền đình, t19, t20) chỉ có câu test; các mục 6.5 (t22 so với q08), 7.2 (t17, t21 so với q11–q13, q16) và 7.3 (t18 so với q14) có cả dev lẫn test.
- Rủi ro rò rỉ: khi chỉnh tham số chunking trên dev ở các mục 7.2 và 6.5, kết quả test cùng mục có thể được lợi gián tiếp. Riêng t17 (prestin, p.316) và q13 (hearing loss, p.317) nằm ở hai trang liền kề.
- Rủi ro đại diện: chương 7 chiếm 5/6 câu test, nên test lệch về thính giác. Mẫu test chỉ có 6 câu, mỗi câu tương đương 16,7 điểm %; không được suy rộng.
- Mỗi câu chỉ có **một** gold page. Script đã xác nhận không có câu trích nào xuất hiện ở trang khác. Nếu khi chấm thủ công thấy trang khác cũng đủ bằng chứng, ghi vào nhãn audit, không sửa gold.

## Tiêu chí chọn chiến lược (chốt trước khi chạy)
Chọn trên **dev** theo thứ tự ưu tiên:
1. Evidence Hit@3 cao nhất;
2. hòa thì xét Hit@1;
3. vẫn hòa thì xét coherence trung bình (10 vị trí);
4. sau đó ưu tiên ít chunk / thời gian ingest thấp hơn.

Chiến lược được chọn chỉ chạy test **một lần** sau khi cố định; ba cấu hình đều được báo cáo trên test để minh bạch.

## Nguồn gốc câu hỏi
AI (Claude) soạn nháp câu hỏi từ text đã trích. Tác giả **đọc lại từng câu và đối chiếu với PDF** (phải ghi xác nhận này vào báo cáo cá nhân). Script kiểm chứng evidence tự động.

## Thay đổi và sự cố
- **2026-11-14, sự cố nhìn thấy dữ liệu test:** khi kiểm tra `store.search` + filter, AI đã chạy query thử *"Which thalamic nucleus relays signals to the cortex?"* trên index B. Câu này gần nghĩa với **t21**. Kết quả thấy được: không filter thì top-3 đều thuộc chương 7 (p.321, p.334, p.321); filter `chapter=6` trả về p.267/271/272. **Không có tham số, câu hỏi hay gold nào được thay đổi sau đó.** Tham số chunking (A: 500/50; B: max 800, min 150) đã chốt trước lần chạy này. Khi diễn giải kết quả t21, cần lưu ý câu này không còn hoàn toàn "chưa nhìn thấy".
