"""Compatibility import for the standalone RAG package."""

import sys

from rag_model import agronomy as _implementation

sys.modules[__name__] = _implementation
