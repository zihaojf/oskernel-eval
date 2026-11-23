"""Local scorer for StarryOS / OS competition disk images.

The package is intentionally standalone: it lives next to the StarryOS
checkout, reads its ``kernel-*`` and ``sdcard-*.img`` files, and never writes
into the kernel repository.
"""

__version__ = "1.0.0"
