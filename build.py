"""
Build the optional YoloFountain SIMD core -> yolofountain/_yolocore.{dll,so,dylib}.

    python build.py

MSVC on Windows (auto-locates vcvarsall), gcc/clang elsewhere. The package runs
fine without this — it's a pure accelerator; when absent, the pure-Python path is
used with identical results.
"""
import glob
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join("yolofountain", "native", "yolo_core.c")


def _cleanup():
    for j in ("yolofountain/_yolocore.exp", "yolofountain/_yolocore.lib",
              "yolofountain/native/yolo_core.obj"):
        try:
            os.unlink(os.path.join(ROOT, j))
        except OSError:
            pass


def build_windows():
    out = os.path.join("yolofountain", "_yolocore.dll")
    pats = [r"C:\Program Files\Microsoft Visual Studio\*\*\VC\Auxiliary\Build\vcvarsall.bat",
            r"C:\Program Files (x86)\Microsoft Visual Studio\*\*\VC\Auxiliary\Build\vcvarsall.bat"]
    vcvars = next((c for p in pats for c in glob.glob(p)), None)
    if not vcvars:
        print("no vcvarsall.bat found — install Visual Studio Build Tools"); return None
    obj = os.path.join("yolofountain", "native", "yolo_core.obj")
    bat = tempfile.NamedTemporaryFile("w", suffix=".bat", delete=False, dir=ROOT)
    bat.write("@echo off\r\n")
    bat.write('call "%s" x64\r\n' % vcvars)
    bat.write('cd /d "%s"\r\n' % ROOT)
    bat.write('cl /nologo /O2 /W3 /LD "%s" /Fe:"%s" /Fo:"%s"\r\n' % (SRC, out, obj))
    bat.close()
    r = subprocess.run(["cmd", "/c", bat.name], cwd=ROOT, capture_output=True, text=True)
    os.unlink(bat.name)
    tail = (r.stdout or "")[-1500:] + (r.stderr or "")[-500:]
    print(tail.strip())
    _cleanup()
    return out


def build_unix():
    out = os.path.join("yolofountain", "_yolocore.dylib" if sys.platform == "darwin"
                       else "_yolocore.so")
    cc = os.environ.get("CC", "cc")
    r = subprocess.run([cc, "-O3", "-shared", "-fPIC", "-o", out, SRC],
                       cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr); return None
    return out


def main():
    out = build_windows() if os.name == "nt" else build_unix()
    if out and os.path.exists(os.path.join(ROOT, out)):
        print("Built", out)
        return 0
    print("build failed"); return 1


if __name__ == "__main__":
    raise SystemExit(main())
