"""The three strategies. Same corpus, embedding model, store, metric (cosine) and benchmark.

A -> B changes only the chunking method.
B -> C changes only the metadata filter (C searches B's index; filter applied only when
     the question carries one).
"""
STRATEGIES = {
    "A": {"chunking": "fixed", "params": {"size": 500, "overlap": 50},
          "index": "A", "use_filter": False,
          "hypothesis": "Baseline: fixed 500-char windows cut sentences and evidence; lower Hit@1/coherence."},
    "B": {"chunking": "paragraph", "params": {"max_size": 800, "min_size": 150},
          "index": "B", "use_filter": False,
          "hypothesis": "Paragraph/heading chunks keep evidence whole -> higher Hit@3 and coherence than A."},
    "C": {"chunking": "paragraph", "params": {"max_size": 800, "min_size": 150},
          "index": "B", "use_filter": True,
          "hypothesis": "Same chunks as B; chapter filter removes cross-chapter distractors on filter questions."},
}
FILTERABLE_FIELDS = {"chapter", "section"}      # whitelist of metadata keys allowed in filters
TOP_K = 3
