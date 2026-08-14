"""OS clipboard access for flock. Windows via pywin32; a fake for tests/headless."""
import time


class FakeClipboard:
    def __init__(self, value=None):
        self._v = value

    def get(self):
        return self._v

    def set(self, text):
        self._v = text


class WindowsClipboard:
    """Text clipboard via pywin32, with a small retry (the clipboard is a shared,
    single-owner resource — another app may hold it for a moment)."""
    def _open(self):
        import win32clipboard
        for _ in range(10):
            try:
                win32clipboard.OpenClipboard()
                return win32clipboard
            except Exception:
                time.sleep(0.02)
        raise RuntimeError("clipboard busy")

    def get(self):
        import win32con
        cb = self._open()
        try:
            if cb.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                return cb.GetClipboardData(win32con.CF_UNICODETEXT)
            return None
        finally:
            cb.CloseClipboard()

    def set(self, text):
        import win32con
        cb = self._open()
        try:
            cb.EmptyClipboard()
            cb.SetClipboardData(win32con.CF_UNICODETEXT, text)
        finally:
            cb.CloseClipboard()


def default_clipboard():
    try:
        import win32clipboard  # noqa: F401
        return WindowsClipboard()
    except Exception:
        return FakeClipboard()
