"""vidtheque GPU inference worker.

Stateless HTTP inference API (STT / embeddings / OCR) with a single lifecycle
manager owning the GPU. Never imported by the ``mcp`` package — the two talk
over HTTP only.
"""

__version__ = "0.0.3"

__all__ = ["__version__"]
