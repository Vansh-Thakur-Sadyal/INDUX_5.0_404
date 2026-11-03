import unittest
from unittest.mock import Mock

from backend.api.utils.response_guard import (
    concise_response,
    guard_response,
    has_generic_deflection,
)


class ResponseGuardTests(unittest.TestCase):
    def test_actionable_reply_passes_without_retry(self):
        retry = Mock()
        reply = "For Rabi, start with onion and potato based on your soil readings."

        result = guard_response(reply, retry, "Rule-based answer.")

        self.assertEqual(result, reply)
        retry.assert_not_called()

    def test_generic_deflection_triggers_json_retry(self):
        retry = Mock(return_value='{"reply":"Rank onion, potato, and capsicum for Rabi."}')

        result = guard_response(
            "These are examples, not recommendations. Ask a local extension.",
            retry,
            "Rule-based answer.",
        )

        self.assertEqual(result, "Rank onion, potato, and capsicum for Rabi.")
        retry.assert_called_once()

    def test_rejected_or_invalid_retry_uses_the_deterministic_answer(self):
        for retry_response in (
            '{"reply":"I cannot recommend crops from this data."}',
            "not JSON",
        ):
            with self.subTest(retry_response=retry_response):
                result = guard_response(
                    "I can't confirm the best crop.",
                    lambda: retry_response,
                    "Assuming Rabi, investigate wheat, mustard, and chickpea.",
                )
                self.assertEqual(
                    result,
                    "Assuming Rabi, investigate wheat, mustard, and chickpea.",
                )

    def test_retry_provider_failure_returns_the_deterministic_answer(self):
        result = guard_response(
            "I can't confirm the best crop.",
            Mock(side_effect=RuntimeError("provider unavailable")),
            "Assuming Rabi, investigate wheat, mustard, and chickpea.",
        )

        self.assertIn("investigate wheat", result)

    def test_guard_detects_the_requested_forbidden_phrases(self):
        self.assertTrue(has_generic_deflection("I can't confirm this."))
        self.assertTrue(has_generic_deflection("I'm not sure about the crop."))
        self.assertTrue(has_generic_deflection("These are examples, not recommendations."))
        self.assertFalse(has_generic_deflection("Assuming Rabi, grow onion first."))

    def test_concise_response_limits_sentences_and_requested_lists(self):
        self.assertEqual(
            concise_response("Direct answer. Reason. Next step. Extra detail."),
            "Direct answer. Reason. Next step.",
        )
        self.assertEqual(
            concise_response("Options:\n- One\n- Two\n- Three\n- Four"),
            "Options:\n- One\n- Two\n- Three",
        )