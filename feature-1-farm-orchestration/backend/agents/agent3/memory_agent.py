# ==========================================
# AGENT 5 (MEMORY + LEARNING AGENT)
# ==========================================

import json
import os
from datetime import datetime

MEMORY_FILE = os.path.join(os.path.dirname(__file__), "memory.json")


# ==========================================
# LOAD MEMORY
# ==========================================

def load_memory():
    if not os.path.exists(MEMORY_FILE):
        return []

    try:
        with open(MEMORY_FILE, "r") as f:
            return json.load(f)
    except:
        return []


# ==========================================
# SAVE MEMORY
# ==========================================

def save_memory(entry):
    memory = load_memory()
    memory.append(entry)

    with open(MEMORY_FILE, "w") as f:
        json.dump(memory[-50:], f, indent=2)  # keep last 50 runs


# ==========================================
# 🧠 LLM LEARNING
# ==========================================

def learn_from_run(current_run):

    memory = load_memory()

    try:
        import sys
        sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
        from llm_client import mistral_chat

        insight = mistral_chat([{
            "role": "user",
            "content": f"""
            You are a learning AI.

            Past runs:
            {memory[-5:]}

            Current run:
            {current_run}

            Analyze patterns and give:
            - improvement suggestions
            - mistakes to avoid
            - optimization ideas

            Keep it short.
            """
        }])

    except Exception:
        insight = "Learning unavailable (LLM failed)"

    # Save memory
    save_memory({
        "timestamp": datetime.utcnow().isoformat(),
        "run": current_run,
        "insight": insight
    })

    return insight
