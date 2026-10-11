import unittest

from trace_root_cause_span_annotator import annotate_root_cause, find_root_cause


class TestFindRootCause(unittest.TestCase):
    def test_no_error_returns_none(self):
        spans = [{"span_id": "a", "parent_id": None}]
        self.assertIsNone(find_root_cause(spans))

    def test_single_error_span(self):
        spans = [{"span_id": "a", "parent_id": None, "error": True}]
        self.assertIs(find_root_cause(spans), spans[0])

    def test_deepest_of_two_at_different_depths(self):
        spans = [
            {"span_id": "root", "parent_id": None, "error": True},
            {"span_id": "mid", "parent_id": "root", "error": True},
            {"span_id": "leaf", "parent_id": "mid", "error": True},
        ]
        self.assertEqual(find_root_cause(spans)["span_id"], "leaf")

    def test_shallow_error_when_leaf_is_clean(self):
        spans = [
            {"span_id": "root", "parent_id": None, "error": True},
            {"span_id": "mid", "parent_id": "root", "error": False},
            {"span_id": "leaf", "parent_id": "mid", "error": False},
        ]
        self.assertEqual(find_root_cause(spans)["span_id"], "root")

    def test_tie_picks_first_in_input_order(self):
        spans = [
            {"span_id": "a", "parent_id": None, "error": True},
            {"span_id": "b", "parent_id": None, "error": True},
        ]
        self.assertEqual(find_root_cause(spans)["span_id"], "a")

    def test_missing_error_key_treated_as_no_error(self):
        spans = [{"span_id": "a", "parent_id": None}]
        self.assertIsNone(find_root_cause(spans))

    def test_parent_reference_to_missing_id_is_treated_as_root(self):
        spans = [{"span_id": "orphan", "parent_id": "ghost", "error": True}]
        self.assertEqual(find_root_cause(spans)["span_id"], "orphan")


class TestAnnotate(unittest.TestCase):
    def test_marks_candidate_and_ancestors(self):
        spans = [
            {"span_id": "root", "parent_id": None},
            {"span_id": "mid", "parent_id": "root"},
            {"span_id": "leaf", "parent_id": "mid", "error": True},
        ]
        self.assertTrue(annotate_root_cause(spans))
        by_id = {s["span_id"]: s for s in spans}
        self.assertEqual(by_id["leaf"]["root_cause"], "candidate")
        self.assertEqual(by_id["mid"]["root_cause"], "candidate")
        self.assertEqual(by_id["root"]["root_cause"], "candidate")

    def test_no_error_marks_all_false_and_returns_false(self):
        spans = [
            {"span_id": "a", "parent_id": None},
            {"span_id": "b", "parent_id": "a"},
        ]
        self.assertFalse(annotate_root_cause(spans))
        self.assertFalse(spans[0]["root_cause"])
        self.assertFalse(spans[1]["root_cause"])

    def test_sibling_of_candidate_is_false(self):
        spans = [
            {"span_id": "root", "parent_id": None},
            {"span_id": "good", "parent_id": "root"},
            {"span_id": "bad", "parent_id": "root", "error": True},
        ]
        annotate_root_cause(spans)
        by_id = {s["span_id"]: s for s in spans}
        self.assertEqual(by_id["bad"]["root_cause"], "candidate")
        self.assertEqual(by_id["root"]["root_cause"], "candidate")
        self.assertFalse(by_id["good"]["root_cause"])

    def test_dangling_parent_stops_walk_without_raising(self):
        spans = [
            {"span_id": "root", "parent_id": None, "error": True},
            {"span_id": "child", "parent_id": "ghost", "error": True},
        ]
        # child is deepest (depth 1) and becomes candidate; its parent is
        # missing so no further ancestors are marked.
        annotate_root_cause(spans)
        self.assertEqual(spans[1]["root_cause"], "candidate")
        self.assertFalse(spans[0]["root_cause"])

    def test_cycle_raises_value_error(self):
        spans = [
            {"span_id": "a", "parent_id": "b", "error": True},
            {"span_id": "b", "parent_id": "a"},
        ]
        with self.assertRaises(ValueError):
            annotate_root_cause(spans)

    def test_non_bool_error_value_is_rejected(self):
        spans = [{"span_id": "a", "parent_id": None, "error": "yes"}]
        with self.assertRaises(ValueError):
            annotate_root_cause(spans)

    def test_non_list_input_raises_type_error(self):
        with self.assertRaises(TypeError):
            annotate_root_cause({"span_id": "a"})  # type: ignore[arg-type]

    def test_non_dict_span_raises_type_error(self):
        with self.assertRaises(TypeError):
            annotate_root_cause(["not-a-span"])  # type: ignore[list-item]


if __name__ == "__main__":
    unittest.main()
