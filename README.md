# trace-root-cause-span-annotator

Post-processes an in-memory span tree to identify the deepest error span and annotates ancestor spans with a root-cause marker for faster incident triage.

## Usage

```python
from trace_root_cause_span_annotator import annotate_root_cause, find_root_cause

spans = [
    {"span_id": "root", "parent_id": None},
    {"span_id": "mid",   "parent_id": "root"},
    {"span_id": "leaf", "parent_id": "mid", "error": True},
]

found = find_root_cause(spans)
# found is the same dict object as spans[2]

annotate_root_cause(spans)
# spans[0]["root_cause"] == "candidate"
# spans[1]["root_cause"] == "candidate"
# spans[2]["root_cause"] == "candidate"
```

## Why this exists

When a trace fans out into many parallel branches, the error that *surfaced* is often not the error that *caused* the incident — every downstream span that observes the failure tends to mark itself as errored too. This library walks the tree and picks the **deepest** error span, on the reasoning that the unit of work closest to the leaf is the most specific failure point. Ancestors of that span are marked so an on-call engineer scanning top-down sees the breadcrumb path to the candidate without having to expand every branch.

The trade-off: "deepest" is not always "the" root cause — a mid-tree timeout that cascades into leaf I/O errors will point at the leaves, not the timeout. We chose depth because it is cheap, deterministic, and rarely worse than the alternative ("first error seen") for the common leaf-failure case. If you need causal ordering, run a dedicated RCA engine on the annotated subset.

## Awkward edges

- **Ties.** When two error spans share the deepest depth, the one appearing first in the input list wins. No tie-breaking on start time, because we do not assume spans carry timestamps.
- **Missing `error` key.** Treated as `False` (no error). A non-bool `error` value raises `ValueError` at validation time rather than being silently coerced.
- **Dangling `parent_id`.** If a span references a parent that is not in the list, the ancestor walk stops there. The candidate is still marked.
- **Cycles.** A `parent_id` chain that loops back on itself raises `ValueError`. A cyclic span tree is a producer bug worth surfacing rather than papering over.
- **Mutation.** `annotate_root_cause` sets `root_cause` on every span in the input list — `"candidate"` for the winner and its ancestors, `False` otherwise. It does not copy.
