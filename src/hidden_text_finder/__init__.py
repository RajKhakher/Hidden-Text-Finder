"""Hidden-Text Finder: find text that people can't see but AI tools still read."""

from .models import Finding, ScanError, ScanResult
from .scanner import SUPPORTED_EXTENSIONS, scan_bytes, scan_file
from .version import __version__

__all__ = ["Finding", "ScanError", "ScanResult", "SUPPORTED_EXTENSIONS", "scan_bytes", "scan_file", "__version__"]
