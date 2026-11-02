# utils/mistral_integration.py

import json
import os
import sys
from typing import Dict, Any, Optional

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))


class MistralValidator:
    """
    Uses the Mistral API (model set in backend/llm_client.py) to
    perform intelligent validation on field data.

    Features:
    - Detect anomalies
    - Validate consistency
    - Provide structured feedback
    """

    # -----------------------------------
    # INTERNAL: CALL MISTRAL API
    # -----------------------------------
    def _query_llm(self, prompt: str) -> Optional[str]:
        """
        Sends prompt to the Mistral API and returns response text.
        """

        try:
            from llm_client import mistral_chat

            return mistral_chat(
                [{"role": "user", "content": prompt}],
                json_mode=True,
            )

        except Exception:
            return None

    # -----------------------------------
    # VALIDATE FIELD DATA
    # -----------------------------------
    def validate(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validates a single standardized record using LLM.

        Returns:
            dict:
            {
                "is_valid": bool,
                "issues": [...],
                "suggestions": [...]
            }
        """

        prompt = f"""
You are an agricultural data validation assistant.

Analyze the following field data and detect:
- anomalies
- inconsistencies
- missing critical values

Return ONLY valid JSON in this format:
{{
  "is_valid": true/false,
  "issues": ["..."],
  "suggestions": ["..."]
}}

DATA:
{json.dumps(record, indent=2)}
"""

        response_text = self._query_llm(prompt)

        if not response_text:
            return {
                "is_valid": True,
                "issues": ["LLM validation skipped"],
                "suggestions": [],
            }

        # Parse JSON safely
        try:
            result = json.loads(response_text)
            return result

        except json.JSONDecodeError:
            return {
                "is_valid": True,
                "issues": ["Invalid LLM response format"],
                "suggestions": [],
            }
