"""Blind review of the audit labels by the author (interactive, run in your own terminal).

Run from starter/:  python -m scripts.review_labels
For coherence and grounding rows: the content is shown WITHOUT the AI label; you type your
own label first, then the AI label is revealed. Agreement/disagreement is recorded.
For metadata rows: you spot-check 5 random rows (fixed seed) against the PDF, then confirm.
Progress is saved after every row; quit with q and run again to continue.
Writes the CSVs in results/ and results/review_log.json (agreement statistics).
"""
import csv
import json
import os
import random
import textwrap
from pathlib import Path

RES = Path(__file__).resolve().parents[1] / "results"
LOG = RES / "review_log.json"
SCALE = {
    "coherence": "0 = cắt giữa ý / thiếu chủ thể, không hiểu | 1 = hiểu một phần, thiếu ngữ cảnh | 2 = tự hiểu, giữ trọn ý",
    "grounding": "0 = không có bằng chứng | 1 = có nhưng thiếu điều kiện/ngữ cảnh | 2 = đủ để trả lời và truy nguồn",
}


def load(name):
    with (RES / name).open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def save(name, rows):
    with (RES / name).open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def wrap(text, width=110):
    return "\n".join(textwrap.fill(p, width) for p in text.split("\n") if p.strip())


def ask(prompt, allowed):
    while True:
        v = input(prompt).strip().lower()
        if v in allowed:
            return v
        print(f"  -> nhập một trong: {', '.join(allowed)}")


def review_scored(kind, name, show, author, log):
    rows = load(name)
    todo = [r for r in rows if r["labeled_by"].startswith(("AI draft", "LLM judge"))]
    print(f"\n===== {name}: {len(todo)}/{len(rows)} dòng chưa duyệt =====\nThang: {SCALE[kind]}")
    for i, r in enumerate(todo, 1):
        print("\n" + "-" * 110 + f"\n[{i}/{len(todo)}]")
        show(r)
        mine = ask("\nNhãn CỦA BẠN (0/1/2, s = bỏ qua, q = dừng): ", ["0", "1", "2", "s", "q"])
        if mine == "q":
            return False
        if mine == "s":
            continue
        ai = r["label"]
        print(f"  Nhãn AI: {ai} | lý do AI: {r['reason']}")
        if mine == ai:
            note = input("  Trùng nhãn. Lý do của bạn (Enter = giữ lý do AI): ").strip()
            r["reason"] = note or r["reason"]
            r["labeled_by"] = f"{author} (blind review, agrees with AI draft)"
            log[kind]["agree"] += 1
        else:
            note = ""
            while not note:
                note = input("  KHÁC nhãn AI. Lý do của bạn (bắt buộc): ").strip()
            r["label"], r["reason"] = mine, f"{note} [AI draft was {ai}]"
            r["labeled_by"] = f"{author} (blind review, changed AI draft {ai}->{mine})"
            log[kind]["changed"].append({"key": {k: r[k] for k in ("position_id", "index", "strategy", "id") if k in r},
                                         "ai": ai, "author": mine})
        save(name, rows)
        LOG.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


def show_coherence(r):
    print(f"Vị trí {r['position_id']} | index {r['index']} (áp dụng cho {r['applies_to']}) | {r['chunk_id']} | {r['chunk_len']} ký tự")
    print(f"Ngữ cảnh nguồn quanh vị trí: …{r['source_context']}…\n--- CHUNK ---\n{wrap(r['chunk_text'])}")


def show_grounding(r):
    print(f"{r['strategy']} / {r['id']} | gold page {r['gold_page']} | filter {r['filter_applied'] or '-'} "
          f"| auto Evidence Hit@3 = {r['auto_evidence_hit_at_3']}")
    print(f"CÂU HỎI: {r['question']}\nĐÁP ÁN MONG ĐỢI: {r['expected_answer']}")
    for k in ("top1", "top2", "top3"):
        print(f"\n--- {k.upper()} ---\n{wrap(r.get(k, ''))}")


def review_metadata(author, log):
    rows = load("audit_metadata.csv")
    if not any(r["labeled_by"].startswith(("AI draft", "LLM judge")) for r in rows):
        print("\naudit_metadata.csv: đã duyệt.")
        return True
    sample = random.Random(6).sample(range(len(rows)), 5)
    print("\n===== audit_metadata.csv: 45 dòng =====\nCác cột auto_* đã đối chiếu với PDF. Mở PDF "
          "(starter/data/...pdf) và kiểm tra 5 dòng ngẫu nhiên dưới đây:")
    ok_ids = []
    for n, i in enumerate(sample, 1):
        r = rows[i]
        print(f"\n[{n}/5] {r['strategy']} {r['id']} rank {r['rank']} | {r['chunk_id']}\n"
              f"  Mở PDF trang {r['page']}: số in chân trang phải là {r['printed_page']}; chương {r['chapter']}; "
              f"mục '{r['section']}'\n  auto: page_text={r['auto_page_text_in_pdf_page']} chapter={r['auto_chapter_ok']} "
              f"section={r['auto_section_ok']} filter={r['auto_filter_ok'] or '-'}")
        v = ask("  Đúng hết? (y/n, q = dừng): ", ["y", "n", "q"])
        if v == "q":
            return False
        if v == "n":
            r["label"] = "0"
            r["reason"] = input("  Sai ở đâu? ").strip() or "author found an error"
            r["labeled_by"] = f"{author} (manual PDF check: error)"
            log["metadata"]["errors"].append(r["chunk_id"])
        ok_ids.append(f"{r['strategy']}/{r['id']}/rank{r['rank']}")
    if ask("\nÁp dụng 'đã duyệt' cho các dòng còn lại (kiểm tra tự động + 5 dòng kiểm tay)? (y/n): ", ["y", "n"]) == "y":
        for i, r in enumerate(rows):
            if r["labeled_by"].startswith(("AI draft", "LLM judge")):
                how = "manual PDF spot-check" if i in sample else "auto PDF check, reviewed"
                r["labeled_by"] = f"{author} ({how})"
        log["metadata"]["spot_checked"] = ok_ids
    save("audit_metadata.csv", rows)
    LOG.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


def main():
    os.system("")  # enable ANSI on Windows consoles
    author = input("Tên người chấm (ghi vào cột labeled_by): ").strip() or "Tác giả"
    log = json.loads(LOG.read_text(encoding="utf-8")) if LOG.exists() else {
        "coherence": {"agree": 0, "changed": []}, "grounding": {"agree": 0, "changed": []},
        "metadata": {"errors": [], "spot_checked": []}}
    if not review_scored("coherence", "audit_coherence.csv", show_coherence, author, log):
        return
    if not review_scored("grounding", "audit_grounding.csv", show_grounding, author, log):
        return
    if not review_metadata(author, log):
        return
    for k in ("coherence", "grounding"):
        n = log[k]["agree"] + len(log[k]["changed"])
        print(f"{k}: đồng ý AI {log[k]['agree']}/{n}, sửa {len(log[k]['changed'])}")
    print("Xong. Chạy tiếp: python -m scripts.summarize_audit")


if __name__ == "__main__":
    main()
