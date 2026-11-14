"""Write the AI-drafted audit labels into results/audit_*.csv (labeled_by marks them as drafts).

Run once from starter/:  python -m scripts.apply_draft_labels
Method: Claude (claude-opus-5-5) read every sampled chunk / top-3 set in full and applied the
GUIDE §4 rubric. The author must review each row, edit `label`/`reason` where they disagree
and change `labeled_by` to their name. Then run `python -m scripts.summarize_audit`.
Rows whose labeled_by no longer starts with "AI draft" are never overwritten.
"""
import csv
from pathlib import Path

RES = Path(__file__).resolve().parents[1] / "results"
TAG = "AI draft (Claude) - pending author review"

COHERENCE = {  # (position_id, index): (label, reason)
    ("p251@338", "A"): (1, "Chapter outline, but cut mid-word at the end ('6.6 Unsolved Quest')."),
    ("p251@338", "B"): (2, "Whole chapter opener: title, figure caption and complete outline."),
    ("p252@1535", "A"): (1, "Starts mid-sentence ('from tree branches and their insect prey') without the subject (bats); ends with a cut heading '# 6.1 An Overview of the Visual'."),
    ("p252@1535", "B"): (2, "Complete bat-echolocation analogy paragraph; understandable alone (final concluding sentence is in the next chunk)."),
    ("p253@896", "A"): (1, "Starts mid-word ('e. Shorter wavelengths'), mixes the light-spectrum topic with the eye-anatomy heading, ends cut ('FIGURE 6.3 Anatomy of the human')."),
    ("p253@896", "B"): (2, "Heading 'Anatomy of the Eye' plus complete paragraphs on the optics and image inversion."),
    ("p254@957", "A"): (1, "Starts mid-word ('hest visual acuity'); ends with a cut heading ('Sharp vision requ')."),
    ("p254@957", "B"): (2, "Two complete paragraphs (fovea fixation, choroid/sclera); readable alone."),
    ("p255@637", "A"): (1, "Starts mid-word ('y objects such as pages of text'), then section 6.2 heading and learning objectives cut mid-sentence."),
    ("p255@637", "B"): (1, "Heading + objectives are complete, but the chunk ends mid-sentence ('create a neural signal, a') because the sentence continues on p.256: page-level chunking limitation."),
    ("p256@1582", "A"): (1, "Starts with a fragment ('ver the retina next to the ganglion cells.'), ends mid-sentence ('have the same general structure,')."),
    ("p256@1582", "B"): (2, "Figure caption + complete paragraph on why retinas are studied (Cajal, mudpuppy)."),
    ("p257@600", "A"): (1, "Starts with an image-credit URL fragment; paragraph cut mid-word ('mitochond')."),
    ("p257@600", "B"): (2, "Heading + complete paragraph on photoreceptor structure."),
    ("p258@889", "A"): (1, "Starts '120 million rods' without the subject sentence; ends with a cut heading ('...Hyperpolarize in th')."),
    ("p258@889", "B"): (2, "Heading + complete paragraph on the 'backward' photoreceptor response, including the closing remark."),
    ("p259@799", "A"): (1, "Starts mid-word ('ater degrees of hyperpolarization'), ends mid-word ('interrupts transmi')."),
    ("p259@799", "B"): (2, "Self-contained paragraph on glutamate release in the dark."),
    ("p260@354", "A"): (1, "Phototransduction paragraph cut before its key point ('leads to a series of steps that ...'); the sodium-channel closure is missing."),
    ("p260@354", "B"): (2, "Complete phototransduction paragraph including the key point (closure of sodium channels)."),
}

GROUNDING = {  # (strategy, id): (label, reason)
    ("A", "t17"): (2, "Top-1 (p316 f01) states prestin and 'up to 50 kHz'; traceable to p.316."),
    ("A", "t18"): (1, "Only p323 f02 says level differences matter more at high frequencies; the reason (head shadow, low frequencies diffract) is missing."),
    ("A", "t19"): (2, "Top-3 (p334 f00) contains the full statement on otolith organs."),
    ("A", "t20"): (2, "Top-1 (p337 f00) contains the gain definition and its dependence on object distance."),
    ("A", "t21"): (2, "Top-1 (p321 f01) says MGB is 'a nucleus in the dorsal thalamus, and is the primary gateway for auditory information to reach the' (cut); f00 confirms MGB is a thalamic nucleus. Enough to answer and trace, although the automatic evidence hit is False (quote split at char 950)."),
    ("B", "t17"): (2, "Top-1 (p316 p00) contains prestin and 50 kHz with the outer-hair-cell context."),
    ("B", "t18"): (2, "Top-3 (p323 p00) gives head shadow + why the effect is stronger at high frequencies; top-1 adds ITD vs ILD context."),
    ("B", "t19"): (2, "Top-2 (p334 p01) contains the full statement plus utricle/saccule detail."),
    ("B", "t20"): (2, "Top-1 (p337 p00) contains the complete answer."),
    ("B", "t21"): (2, "Top-1 (p321 p01) complete MGB paragraph; note top-2 is the competing LGN chunk from Chapter 6 (no filter)."),
    ("C", "t17"): (2, "Same as B (no filter on this question)."),
    ("C", "t18"): (2, "Same as B (no filter on this question)."),
    ("C", "t19"): (2, "Same as B (no filter on this question)."),
    ("C", "t20"): (2, "Same as B (no filter on this question)."),
    ("C", "t21"): (2, "Top-1 complete MGB paragraph; with chapter=7 filter the LGN distractor is gone and top-3 (p321 p02) adds the auditory-cortex context."),
}


def apply(path: Path, keys: list[str], labels: dict, default=None) -> None:
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    n = 0
    for r in rows:
        if r.get("labeled_by") and not r["labeled_by"].startswith("AI draft"):
            continue                                # author already reviewed: keep
        k = tuple(r[c] for c in keys)
        val = labels.get(k) if labels else default(r)
        if val:
            r["label"], r["reason"], r["labeled_by"] = str(val[0]), val[1], TAG
            n += 1
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"{path.name}: {n} draft labels written")


def metadata_label(r: dict):
    auto = [r[c].strip().lower() == "true" for c in ("auto_source_ok", "auto_page_text_in_pdf_page", "auto_chapter_ok", "auto_section_ok")]
    if r["filter_applied"]:
        auto.append(r["auto_filter_ok"].strip().lower() == "true")
    if all(auto) and r["printed_page"]:
        return (1, f"source/page/chapter/section correct: chunk text found on PDF p.{r['page']} "
                   f"(printed {r['printed_page']}), section '{r['section']}' present on that page")
    return (0, "a metadata check failed: see auto_* columns")


if __name__ == "__main__":
    apply(RES / "audit_coherence.csv", ["position_id", "index"], COHERENCE)
    apply(RES / "audit_grounding.csv", ["strategy", "id"], GROUNDING)
    apply(RES / "audit_metadata.csv", [], {}, default=metadata_label)
