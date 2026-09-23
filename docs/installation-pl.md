# Instalacja

[English](installation.md) | Polski

Windows jest głównym i realnie zwalidowanym targetem instalacji. Linux jest
drugorzędnym zaimplementowanym targetem z native validation w toku. Obsługa
macOS jest zaimplementowana i sprawdzana w nieinteraktywnym CI, ale końcowa
walidacja na fizycznym interaktywnym Macu nadal pozostaje do wykonania.

## Wymagania

- CPython 3.11 lub nowszy.
- Codex z dostępem do użytkownikowego `config.toml`.
- Lokalny checkout tego repozytorium.
- Zależności opcjonalne tylko dla funkcji, których chcesz używać.

Setup scripts odrzucają interpretery Pythona, których ścieżka wskazuje na
runtime lokalnego modelu. Codex Local Ops powinien używać własnego zwykłego
środowiska CPython.

## Windows — główna ścieżka

Otwórz zwykły Windows PowerShell w katalogu repozytorium i uruchom:

```powershell
.\setup.ps1
```

Domyślnie skrypt:

1. Wyszukuje niezależny CPython 3.11+.
2. Tworzy lub wykorzystuje `%USERPROFILE%\.codex-local-ops.venv`.
3. Instaluje bieżące repozytorium do tego venv.
4. Tworzy konfigurację Local Ops pod `%USERPROFILE%\.codex-local-ops`.
5. Wykonuje backup `%USERPROFILE%\.codex\config.toml`.
6. Zastępuje wyłącznie wpis `mcp_servers.codexLocalOps`.
7. Uruchamia diagnostykę i `codex mcp list`, chyba że użyto `-SkipDiagnostics`.

Przydatne parametry:

```powershell
.\setup.ps1 -PythonExe "C:\Path\To\python.exe"
.\setup.ps1 -TrustedRoot "C:\Projects"
.\setup.ps1 -InstallRoot "C:\LocalOps" -VenvRoot "C:\LocalOpsVenv"
.\setup.ps1 -SkipDiagnostics
```

Domyślny setup instaluje core. Zwalidowane funkcje Windows desktop/browser
zainstaluj do tego samego venv z katalogu repozytorium:

```powershell
$LocalOpsPython = Join-Path $env:USERPROFILE '.codex-local-ops.venv\Scripts\python.exe'
& $LocalOpsPython -m pip install --upgrade ".[desktop,browser,windows]"
& $LocalOpsPython -m playwright install chromium
```

Opcjonalna integracja OBS:

```powershell
& $LocalOpsPython -m pip install --upgrade ".[obs]"
```

FFmpeg jest zależnością systemową. Zainstaluj go osobno i upewnij się, że
`ffmpeg` oraz `ffprobe` są dostępne w `PATH` przed użyciem narzędzi media.

Jeśli execution policy blokuje skrypt, można jednorazowo uruchomić go dla
bieżącego procesu bez zmiany polityki całego systemu:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1
```

Po instalacji wykonaj First Run:

```powershell
clops manager
```

lub:

```powershell
clops wizard
```

Skonfiguruj co najmniej jeden trusted root. Zobacz [windows.md](windows.md),
gdzie opisano zwalidowaną ścieżkę desktop/browser i walidację interaktywną.

## Linux

W katalogu repozytorium:

```sh
chmod +x setup.sh
./setup.sh
```

Skrypt tworzy venv użytkownika pod Local Ops home, instaluje bieżące źródła,
tworzy konfigurację, wykonuje backup `~/.codex/config.toml` i rejestruje serwer
STDIO MCP.

Opcjonalne funkcje Pythona można doinstalować interpreterem z venv Local Ops,
np.:

```sh
~/.codex-local-ops/.venv/bin/python -m pip install --upgrade ".[desktop,browser,obs]"
~/.codex-local-ops/.venv/bin/python -m playwright install chromium
```

Desktop zależy od rzeczywistej sesji Linux. X11 może wymagać `wmctrl` i
`xdotool`. Ogólna globalna kontrola na Wayland jest świadomie ograniczona.
Zobacz [linux.md](linux.md).

## macOS

Entry point instalacji jest taki sam:

```sh
chmod +x setup.sh
./setup.sh
```

Opcjonalne funkcje instaluje się do venv Local Ops analogicznie jak na Linux.
Funkcje desktopowe mogą wymagać uprawnień Accessibility, Automation i Screen
Recording w System Settings.

macOS nie ma jeszcze statusu fully native-validated. GitHub-hosted macOS CI
sprawdza compile, unit, STDIO MCP, wykrywanie platformy i build paczki. Zobacz
[macos.md](macos.md).

## First Run i konfiguracja

Użyj:

```text
clops manager
clops wizard
```

Manager/wizard konfiguruje trusted roots, profil lokalnego wykonania,
computer-control mode, import SSH i uprawnienia hostów oraz stan funkcji
opcjonalnych. Na systemach headless użyj terminalowego wizarda albo świadomie
edytuj wygenerowany YAML.

Kanał przeglądarki ustawia się w `browser.channel`. Dozwolone wartości zależą
od zainstalowanej wersji Playwright, np. `chromium`, `chrome` lub `msedge`, jeśli
odpowiednia przeglądarka jest dostępna.

## Sprawdzenie rejestracji MCP

Uruchom:

```text
codex mcp list
```

Następnie wykonaj read-only `platform_info` przez zarejestrowany serwer
`codexLocalOps`. Na Windows dostępny jest również interaktywny validator
opisany w [windows.md](windows.md).

## Aktualizacja

Zaktualizuj checkout źródeł, przejrzyj [../CHANGELOG.md](../CHANGELOG.md), a
następnie uruchom ponownie odpowiedni setup script:

```powershell
# Windows
git pull
.\setup.ps1
```

```sh
# Linux/macOS
git pull
./setup.sh
```

Jeśli używasz extras, zaktualizuj je w tym samym venv po aktualizacji core.

## Odinstalowanie

Windows:

```powershell
.\uninstall.ps1
```

Użyj `-RemoveConfig` tylko wtedy, gdy chcesz usunąć również konfigurację.

Linux/macOS:

```sh
./uninstall.sh
```

Argument `--remove-config` usuwa także katalog konfiguracji Local Ops. Oba
uninstallery domyślnie zachowują konfigurację użytkownika.

## Troubleshooting

Zobacz [troubleshooting.md](troubleshooting.md). Instrukcje platformowe są w
[windows.md](windows.md), [linux.md](linux.md) oraz [macos.md](macos.md).
