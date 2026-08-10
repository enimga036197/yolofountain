@echo off
REM Build the YoloFountain SIMD core into yolofountain\_yolocore.dll (MSVC).
REM Optional accelerator — the package runs without it. Requires Visual Studio Build Tools.
setlocal
set VCVARS="C:\Program Files\Microsoft Visual Studio\18\Insiders\VC\Auxiliary\Build\vcvarsall.bat"
if not exist %VCVARS% set VCVARS="C:\Program Files\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvarsall.bat"
if not exist %VCVARS% set VCVARS="C:\Program Files (x86)\Microsoft Visual Studio\2019\BuildTools\VC\Auxiliary\Build\vcvarsall.bat"
call %VCVARS% x64 >nul 2>&1

cl /nologo /O2 /W3 /LD yolofountain\native\yolo_core.c ^
   /Fe:yolofountain\_yolocore.dll /Fo:yolofountain\native\yolo_core.obj
del yolofountain\_yolocore.exp yolofountain\_yolocore.lib yolofountain\native\yolo_core.obj 2>nul
if exist yolofountain\_yolocore.dll (echo Built yolofountain\_yolocore.dll) else (echo BUILD FAILED & exit /b 1)
