import unittest
from unittest.mock import patch

from rag_model.assistant import answer_question
from rag_model.demo_api import ChatRequest, chat


class PortableRagPackageTests(unittest.TestCase):
    def test_demo_api_returns_a_short_soil_ranked_crop_list(self):
        response = chat(ChatRequest(
            message="give me a list of alternative vegetables I can grow",
            crop="Tomato",
            farm_context={
                "nitrogen": 42,
                "phosphorus": 28,
                "potassium": 55,
                "soil_ph": 6.8,
            },
        ))

        self.assertIn("Alternatives to Tomato for Rabi", response["reply"])
        self.assertNotIn("Tomato", response["reply"].split(":", 1)[1])
        self.assertLess(len(response["reply"]), 160)

    @patch(
        "rag_model.assistant.mistral_chat",
        return_value="Farm Support shows nearby categories. It uses location. Cards may be examples. Extra unrelated text.",
    )
    def test_general_answer_is_grounded_and_short(self, model):
        response = answer_question("What does Farm Support show?")

        self.assertEqual(
            response["reply"],
            "Farm Support shows nearby categories. It uses location. Cards may be examples.",
        )
        self.assertEqual(response["action_link"], "farm_support.html")
        model.assert_called_once()

    @patch(
        "rag_model.assistant.mistral_chat",
        side_effect=[
            "I'm not sure what this feature does.",
            '{"reply":"Farm Support lists nearby farm-service categories."}',
        ],
    )
    def test_generic_uncertainty_is_retried_and_removed(self, model):
        response = answer_question("What is Farm Support?")

        self.assertEqual(
            response["reply"],
            "Farm Support lists nearby farm-service categories.",
        )
        self.assertNotIn("not sure", response["reply"].casefold())
        self.assertEqual(model.call_count, 2)


if __name__ == "__main__":
    unittest.main()
