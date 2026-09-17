@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "APP_NAME=TOMS SLA MB"
set "ASSET_NAME=TOMS-SLA-MB.exe"
set "UPDATE_REPOSITORY=cuongtm88-blip/TOMS-SLA-MB-Updates"
set "GH_EXE="

if not exist ".venv\Scripts\python.exe" (
    echo [LOI] Hay chay build_windows.bat truoc.
    goto :failed
)
if not exist "dist\%ASSET_NAME%" (
    echo [LOI] Khong tim thay EXE hoac file SHA-256. Hay build lai.
    goto :failed
)
if not exist "dist\%ASSET_NAME%.sha256" (
    echo [LOI] Khong tim thay EXE hoac file SHA-256. Hay build lai.
    goto :failed
)
for /f "delims=" %%V in ('.venv\Scripts\python.exe -c "from version import APP_VERSION; print(APP_VERSION)"') do set "APP_VERSION=%%V"

where gh >nul 2>nul && set "GH_EXE=gh"
if not defined GH_EXE if exist "%ProgramFiles%\GitHub CLI\gh.exe" set "GH_EXE=%ProgramFiles%\GitHub CLI\gh.exe"
if not defined GH_EXE if exist "%LocalAppData%\Programs\GitHub CLI\gh.exe" set "GH_EXE=%LocalAppData%\Programs\GitHub CLI\gh.exe"
if not defined GH_EXE (
    echo [LOI] Chua co GitHub CLI. Hay cai GitHub CLI va chay lai.
    goto :failed
)
"%GH_EXE%" auth status --hostname github.com >nul 2>nul || "%GH_EXE%" auth login --hostname github.com --web --git-protocol https
if errorlevel 1 goto :failed
"%GH_EXE%" release view "v%APP_VERSION%" --repo "%UPDATE_REPOSITORY%" >nul 2>nul && goto :release_exists

echo.
echo Sap phat hanh %APP_NAME% v%APP_VERSION% len https://github.com/%UPDATE_REPOSITORY%
set /p "CONFIRM=Nhap PHAT HANH de tiep tuc: "
if /I not "%CONFIRM%"=="PHAT HANH" exit /b 0
"%GH_EXE%" release create "v%APP_VERSION%" "dist\%ASSET_NAME%#%ASSET_NAME%" "dist\%ASSET_NAME%.sha256#%ASSET_NAME%.sha256" --repo "%UPDATE_REPOSITORY%" --title "%APP_NAME% v%APP_VERSION%" --generate-notes
if errorlevel 1 goto :failed
echo Da phat hanh thanh cong.
exit /b 0

:release_exists
echo [LOI] Release v%APP_VERSION% da ton tai. Hay tang APP_VERSION trong version.py.
:failed
exit /b 1
