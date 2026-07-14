@echo off
setlocal

set "PYTHON_EXE=python"
if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" (
  set "PYTHON_EXE=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
)

if "%~1"=="" (
  echo Usage:
  echo   run_same_phone_check.bat "C:\path\to\document.pdf"
  echo.
  echo You can also drag a PDF file onto this batch file.
  exit /b 1
)

set "INPUT_PDF=%~1"
set "OUTPUT_DIR=%~dpn1_same_phone_report"

"%PYTHON_EXE%" "%~dp0same_phone_pdf_check.py" "%INPUT_PDF%" --output-dir "%OUTPUT_DIR%" --dpi 180 --keep-images

echo.
echo Report folder:
echo %OUTPUT_DIR%
pause
