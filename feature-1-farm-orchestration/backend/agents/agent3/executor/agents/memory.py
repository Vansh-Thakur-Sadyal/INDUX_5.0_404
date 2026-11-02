import json
import os

MEMORY_FILE = "memory.json"


def save_memory(data):
    history = load_memory()
    history.append(data)

    with open(MEMORY_FILE, "w") as f:
        json.dump(history, f, indent=2)


def load_memory():
    if not os.path.exists(MEMORY_FILE):
        return []
    with open(MEMORY_FILE, "r") as f:
        return json.load(f)
