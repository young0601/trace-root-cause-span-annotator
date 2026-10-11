"""Post-process an in-memory span tree to mark the root-cause span.

A "span" is a dict with the keys:

* ``span_id``   (str)   – unique identifier within the tree
* ``parent_id`` (str|None) – ``None`` (or absent) for a root span
* ``error``     (bool)  – whether this span recorded an error

The functions below do NOT copy the input: they mutate span dicts in place
by setting ``root_cause``.  Returning a boolean avoids forcing a particular
ownership model on the caller; they can keep a handle on the same list they
passed in.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

__all__ = ["find_root_cause", "annotate_root_cause"]


def _ensure_shape(span: Any, *, require_id: bool = True) -> None:
    """Validate that *span* is a dict with the expected keys.

    The brief is deliberately permissive about extra keys — spans in the
    wild carry trace data we neither need nor want to touch.  We only check
    the structural minimum so callers can pass rich trace objects through
    unchanged.
    """
    if not isinstance(span, dict):
        raise TypeError("each span must be a dict, got %r" % type(span).__name__)
    if require_id and not isinstance(span.get("span_id"), str):
        raise ValueError("span is missing a string 'span_id'")
    if span.get("error") is not None and not isinstance(span.get("error"), bool):
        raise ValueError("'error' on span %r must be a bool or absent" % span.get("span_id"))


def _build_children(
    spans: List[Dict[str, Any]],
) -> Dict[Optional[str], List[Dict[str, Any]]]:
    """Group *spans* by ``parent_id``.

    A span whose ``parent_id`` is missing or ``None`` is treated as a root
    and bucketed under ``None``.  We use a plain dict rather than a
    ``defaultdict`` so that look-ups on missing keys return an empty list
    without ever mutating the index during traversal.
    """
    children: Dict[Optional[str], List[Dict[str, Any]]] = {}
    for span in spans:
        _ensure_shape(span)
        parent_id = span.get("parent_id")
        if parent_id is not None and not isinstance(parent_id, str):
            raise ValueError("'parent_id' on span %r must be a string or None" % span.get("span_id"))
        children.setdefault(parent_id, []).append(span)
    return children


def _deepest_error(
    spans: List[Dict[str, Any]],
    children: Dict[Optional[str], List[Dict[str, Any]]],
) -> Optional[Dict[str, Any]]:
    """Return the deepest error span, or ``None`` if no span errored.

    We deliberately pick one winner when depths tie: span order is the
    order in the input list, and the first error span at the deepest level
    wins.  "Deepest" is chosen over "closest to the error surface" because
    an error at a leaf almost always points at the failing unit of work;
    ancestor errors tend to be propagation noise.
    """
    # Spans whose `error` key is missing are treated as non-erroring.  We
    # check `span.get("error") is True` (rather than truthiness) so that a
    # stray non-bool value surfaces as "not an error" rather than silently
    # being treated as one.
    error_spans = [s for s in spans if s.get("error") is True]
    if not error_spans:
        return None

    by_id: Dict[str, Dict[str, Any]] = {
        s["span_id"]: s for s in spans if isinstance(s.get("span_id"), str)
    }

    depths: Dict[str, int] = {}

    def depth(span_id: str) -> int:
        cached = depths.get(span_id)
        if cached is not None:
            return cached
        span = by_id.get(span_id)
        if span is None:
            # Defensive: a child referenced a parent that does not exist.
            # Treat it as a root so we make progress instead of looping.
            d = 0
        else:
            parent_id = span.get("parent_id")
            if parent_id is None or parent_id == span_id:
                d = 0
            else:
                # Detect cycles defensively so we raise ValueError instead
                # of hitting Python's recursion limit.  The ancestor walk in
                # ``annotate_root_cause`` surfaces the same bug, but computing
                # depth on a cyclic tree should also fail loudly.
                trail = {span_id}
                current_parent = parent_id
                while current_parent is not None and current_parent in by_id:
                    if current_parent in trail:
                        raise ValueError("cycle detected in span tree at %r" % current_parent)
                    trail.add(current_parent)
                    current_parent = by_id[current_parent].get("parent_id")
                d = depth(parent_id) + 1
        depths[span_id] = d
        return d

    max_depth = -1
    winner: Optional[Dict[str, Any]] = None
    for span in error_spans:
        d = depth(span["span_id"])
        if d > max_depth:
            max_depth = d
            winner = span
    return winner


def _ancestors(
    start: Dict[str, Any],
    by_id: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Return the ancestor chain of *start*, exclusive of *start* itself.

    We walk ``parent_id`` links until we reach a root or detect a cycle.
    Cycle detection is hard-stop, not silent infinite-loop, because a cyclic
    span tree is a producer bug worth surfacing.
    """
    chain: List[Dict[str, Any]] = []
    seen = {start["span_id"]}
    current = start
    while True:
        parent_id = current.get("parent_id")
        if parent_id is None:
            break
        parent = by_id.get(parent_id)
        if parent is None:
            # Dangling parent reference: stop walking.  We do not raise
            # because the deepest-error span was already located; ancestors
            # simply cannot be annotated beyond what exists.
            break
        if parent["span_id"] in seen:
            raise ValueError("cycle detected in span tree at %r" % parent["span_id"])
        seen.add(parent["span_id"])
        chain.append(parent)
        current = parent
    return chain


def find_root_cause(spans: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Return the deepest error span in *spans*, or ``None`` if none errored.

    The returned dict is the same object from *spans* (identity is
    preserved), so callers can attach additional markers on the winner if
    they wish.  The input is not modified.
    """
    if not isinstance(spans, list):
        raise TypeError("spans must be a list of dicts")
    children = _build_children(spans)
    return _deepest_error(spans, children)


def annotate_root_cause(spans: List[Dict[str, Any]]) -> bool:
    """Annotate *spans* in place with a ``root_cause`` marker.

    Behaviour:

    * Every span gets ``root_cause`` set to either ``"candidate"`` or
      ``False``.  Setting an explicit ``False`` (rather than leaving the
      key absent) keeps downstream consumers from having to distinguish
      "not annotated yet" from "annotated as not-the-cause".
    * The deepest error span is marked ``"candidate"``.
    * Each ancestor of the candidate (excluding the candidate itself) is
      also marked ``"candidate"``, so a human scanning top-down sees the
      breadcrumb path to the root cause.
    * If no span errored, all spans are marked ``False`` and ``False`` is
      returned.

    Returns ``True`` if a candidate was found and annotated, else ``False``.

    Cycles in ``parent_id`` links raise ``ValueError``.  Dangling
    ``parent_id`` references are tolerated: the walk simply stops there.
    """
    if not isinstance(spans, list):
        raise TypeError("spans must be a list of dicts")

    children = _build_children(spans)
    by_id: Dict[str, Dict[str, Any]] = {
        s["span_id"]: s for s in spans
    }

    # Initialise every span first so we never leave a caller-visible
    # half-annotated state if something below raises.
    for span in spans:
        span["root_cause"] = False

    winner = _deepest_error(spans, children)
    if winner is None:
        return False

    winner["root_cause"] = "candidate"
    for ancestor in _ancestors(winner, by_id):
        ancestor["root_cause"] = "candidate"
    return True
