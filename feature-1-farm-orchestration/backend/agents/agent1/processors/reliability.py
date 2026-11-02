# processors/reliability.py

from typing import Dict, Any

class ReliabilityScorer:
    """
    Computes a reliability/confidence score for a standardized record.
    Can be based on:
    - completeness of data
    - presence of multi-source inputs
    - optional AI validation metrics
    """

    def __init__(self):
        pass  # No internal state needed

    def compute(self, record: Dict[str, Any]) -> float:
        """
        Computes a reliability score between 0 and 1 for a single record.

        Args:
            record (dict): Standardized field record

        Returns:
            float: reliability score
        """
        # Default scoring based on data source availability
        meta = record.get("meta", {})
        sources = meta.get("data_sources", {})

        # Count available sources
        total_sources = 4  # sensor, weather, image, market
        available_sources = sum(1 for src in ["sensor", "weather", "image", "market"] if sources.get(src))

        score = available_sources / total_sources

        # Optional: weight by AI validation if present
        validation = record.get("validation")
        if validation is not None and isinstance(validation, dict):
            # For example, average confidence from validation results
            avg_confidence = sum(validation.values()) / len(validation) if validation else 0
            # Combine with source-based score
            score = 0.7 * score + 0.3 * avg_confidence

        return round(score, 2)


# -------------------------------
# Example usage
# -------------------------------
if __name__ == "__main__":
    sample_record = {
        "field_id": "F001",
        "meta": {
            "data_sources": {
                "sensor": True,
                "weather": True,
                "image": False,
                "market": True
            }
        },
        "validation": {"ai_check_1": 0.9, "ai_check_2": 0.8}
    }

    scorer = ReliabilityScorer()
    score = scorer.compute(sample_record)
    print(f"Reliability Score: {score}")
