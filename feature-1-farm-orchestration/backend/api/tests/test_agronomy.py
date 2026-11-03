import unittest

from backend.agronomy import (
    exact_crop_reference,
    rank_crops,
    seasonal_fallback,
    season_for_month,
)


class AgronomyRulesTests(unittest.TestCase):
    def test_exact_crop_reference_returns_only_explicit_crop_matches(self):
        record = exact_crop_reference("What NPK range does Tomato need?")

        self.assertIsNotNone(record)
        self.assertEqual(record["crop"], "Tomato")
        self.assertEqual(record["similarity"], 1.0)
        self.assertIsNone(exact_crop_reference("What crops should I plant?"))

    def test_ranked_crop_matches_use_readings_season_and_exclude_current_crop(self):
        results = rank_crops(
            {
                "nitrogen": 42,
                "phosphorus": 28,
                "potassium": 55,
                "soil_ph": 6.8,
            },
            "Tomato",
            "Rabi",
            "mg/kg",
            limit=5,
            vegetables_only=True,
        )

        self.assertEqual(len(results), 5)
        self.assertNotIn("Tomato", [item["crop"] for item in results])
        self.assertEqual(results[0]["crop"], "Onion")
        self.assertTrue(results[0]["season_match"])
        self.assertIn("N 18 mg/kg below", results[0]["evidence"])

    def test_unknown_unit_is_not_compared_as_if_it_were_mg_per_kg(self):
        self.assertEqual(
            rank_crops(
                {"nitrogen": 42, "phosphorus": 28, "potassium": 55},
                None,
                "Rabi",
                "kg/ha",
            ),
            [],
        )

    def test_season_defaults_and_ambiguous_crop_shortlist_are_deterministic(self):
        self.assertEqual(season_for_month(11), "Rabi")
        self.assertEqual(
            seasonal_fallback("Rabi"),
            ["Wheat", "Mustard", "Chickpea (Chana)"],
        )
        self.assertEqual(
            seasonal_fallback("Rabi", vegetables_only=True),
            ["Potato", "Onion", "Capsicum"],
        )