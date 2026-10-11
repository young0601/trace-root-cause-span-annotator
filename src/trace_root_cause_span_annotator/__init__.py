"""Public surface for trace-root-cause-span-annotator.

Only the names re-exported here are part of the supported API.
"""

from .core import annotate_root_cause, find_root_cause

__all__ = ["annotate_root_cause", "find_root_cause"]
