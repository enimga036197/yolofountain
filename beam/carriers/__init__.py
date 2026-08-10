"""Carrier adapters: frame bytes <-> a specific channel's symbols.

The core codec is byte-oriented and knows nothing about any medium. Carriers are
the swappable seam. Included: text carriers (base45 for QR / any text-only decoder,
base64 for general text channels). Real-world non-text carriers live in the
projects that use beam (qrbeam = QR on a screen, magbeam = a magnetic field).
"""
from .text import (base45_encode, base45_decode,
                   base64_encode, base64_decode)

__all__ = ["base45_encode", "base45_decode", "base64_encode", "base64_decode"]
