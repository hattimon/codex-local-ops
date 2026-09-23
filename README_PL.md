[English](README.md) | Polski

# Codex Local Ops

Codex Local Ops to lokalny serwer MCP działający przez STDIO, który daje
Codexowi kontrolowany dostęp do operacji deweloperskich na komputerze, na
którym został zainstalowany. Łączy operacje na plikach i procesach ograniczone
do zaufanych projektów z integracjami zależnymi od platformy: SSH, WSL, Docker,
automatyzacja przeglądarki, automatyzacja pulpitu, multimedia i opcjonalna
obsługa OBS.

Projekt jest przygotowywany do pierwszego publicznego prerelease
`v0.1.0-beta.1`. Windows jest platformą główną i został zwalidowany w prawdziwej
interaktywnej sesji Windows. Backend Linux i macOS są zaimplementowane, ale ich
natywna walidacja desktopowa nadal trwa.

## Status platform

| Platforma | Status | Zakres |
| --- | --- | --- |
| Windows | **Wspierany / zwalidowany** | Główny target. Zwalidowano core MCP, async jobs, browser, FFmpeg/media, WSL, Docker, SSH, GUI Manager, natywną automatyzację okien i screenshoty. Pozostał interaktywny smoke test screen recordingu. |
| Linux | **Zaimplementowany / native validation w toku** | Core i backend Linux są zaimplementowane. X11 jest obsługiwany, gdy dostępne są wymagane narzędzia systemowe. Wayland świadomie raportuje ograniczenia dla nieograniczonej globalnej kontroli. CI obejmuje ścieżki headless/unit/package. |
| macOS | **Zaimplementowany / CI i native validation w toku** | Backend platformy, lista/akcje okien, screenshoty i integracje systemowe są zaimplementowane. GitHub-hosted macOS CI obejmuje walidację nieinteraktywną. Nie wykonano jeszcze końcowej walidacji desktopowej na fizycznym Macu. |

## Najważniejsze możliwości

- Informacje o systemie, health checks, pliki, procesy i kontrolowane wykonanie komend lokalnych.
- Trusted roots oraz jawne profile uprawnień dla wykonania i sterowania komputerem.
- Trwałe async jobs dla operacji, które nie powinny blokować pojedynczego wywołania MCP.
- Inspekcja SSH agenta, import profili hostów SSH i poziomy uprawnień per host.
- Wykrywanie i wykonywanie komend przez WSL na Windows.
- Inspekcja i operacje Docker/Compose.
- Operacje Git używane w lokalnych workflow deweloperskich.
- Automatyzacja przeglądarki przez Playwright, screenshoty, downloady, diagnostyka console/network i recording.
- Natywna lista/kontrola okien oraz screenshoty pulpitu, jeśli pozwala na to backend platformy.
- Narzędzia FFmpeg/ffprobe do inspekcji i obróbki multimediów, również w wariantach async.
- Opcjonalne narzędzia OBS WebSocket do scen, źródeł, nagrywania, streamingu i statusu.
- Opcjonalne adaptery animacji dla zainstalowanych narzędzi, np. Remotion, Manim lub Blender.

Brak zależności opcjonalnej, wymaganych uprawnień, sesji desktopowej albo usługi
zewnętrznej jest raportowany w kontrolowany sposób jako
`CAPABILITY_UNAVAILABLE`.

## Przegląd architektury

```text
Codex / klient MCP
       |
       | STDIO
       v
codex_local_ops.server
       |
       +-- config + trusted roots + kontrole uprawnień
       +-- audyt/redakcja + ograniczone wykonanie procesów
       +-- trwały magazyn async session jobs
       |
       +-- operacje cross-platform
       |     pliki / shell / Git / SSH / Docker
       |
       +-- backend platformy
       |     Windows / Linux / macOS
       |
       +-- adaptery opcjonalne
             browser / desktop / media / OBS / animation
```

Serwer MCP działa lokalnie. Dłuższe komendy mogą zostać uruchomione jako
trwałe session jobs, dzięki czemu klient Web/bridge szybko otrzymuje
`session_id` i odpytuje stan krótkimi wywołaniami.

## Model bezpieczeństwa

Codex Local Ops jest mostem operacyjnym, a nie sandboxem systemu operacyjnego.
Model bezpieczeństwa ogranicza akceptowane operacje i wymaga jawnych ustawień
dla szerszych możliwości.

### Trusted roots

Operacje na plikach projektu są ograniczone do skonfigurowanych zaufanych
katalogów. Kontrole ścieżek rozwiązują ścieżki rzeczywiste i odrzucają traversal
oraz symlink escape poza zaufane katalogi. Trusted roots ustawia się podczas
First Run przez `clops manager` lub `clops wizard`.

### Profile uprawnień

Lokalne wykonanie używa `permissions.local_profile`:

- `SAFE` dla najbardziej ograniczonego workflow lokalnego,
- `DEVELOPER` dla typowych operacji deweloperskich,
- `FULL` dla celowo szerokiego lokalnego wykonania.

Sterowanie browser/desktop ma niezależny `computer_control.mode`: `OFF`, `SAFE`,
`INTERACTIVE` lub `FULL`.

Profile hostów SSH używają poziomów `READ_ONLY`, `OPERATIONS` lub `FULL`.
Profil operacyjny dopuszcza ograniczony zestaw czynności administracyjnych, a
najszerszy zakres SSH zależy dodatkowo od polityki expert mode.

### Granica Git i GitHub

Integracja Git/GitHub celowo ma dwa poziomy. MCP udostępnia wyłącznie inspekcję:
`git_status`, `git_diff`, `git_log`, `git_branch_list`, `git_remote_list`,
`github_auth_status`, `github_repo_view`, `github_workflow_list`,
`github_workflow_runs`, `github_workflow_run_view`, `github_release_list`,
`github_release_view`, `github_pr_list` i `github_pr_view`.

Zmiany repozytorium i GitHub są operacjami lokalnego CLI uruchamianymi przez
użytkownika. Przykłady:

```text
clops git status --path <repo>
clops git commit --path <repo> -m "message" --add <pathspec>
clops git push --path <repo> --remote origin [--branch <branch>] [--async]
clops git publish --path <repo> --remote origin [--branch <branch>]
clops github auth-status
clops github workflow-status --path <repo>
clops github release-create --path <repo> --tag <existing-tag>
```

Lokalne komendy write są ograniczone do trusted repositories, wymagają profilu
lokalnych uprawnień do zapisu (`DEVELOPER`/`FULL` lub istniejącego override
expert mode) i są audytowane z redakcją sekretów. Wąskie API push nie akceptuje
force push ani wymuszających refspeców. Procesy potomne GitHub usuwają ze swojego
środowiska `GH_TOKEN` i `GITHUB_TOKEN`, dzięki czemu mogą korzystać z hostowego
`gh auth` bez zmiany globalnego środowiska użytkownika i bez ujawniania tokenów
w normalnym outputcie. Ten podział jest celową granicą bezpieczeństwa, a nie
brakiem funkcji.

Output komend jest ograniczony, a typowe wzorce sekretów są redagowane przed
normalnym zwróceniem wyniku przez narzędzia. Dane uwierzytelniające OBS są
przechowywane w skonfigurowanym secret store i nie są zwracane w statusach.
Publiczny streaming OBS wymaga jawnego opt-in w konfiguracji i potwierdzenia na
granicy wywołania.

Zobacz [SECURITY.md](SECURITY.md) oraz
[docs/troubleshooting.md](docs/troubleshooting.md).

## Async jobs

Dla komend, które mogą trwać dłużej niż zwykłe wywołanie MCP, używaj:

- `job_start`
- `run_script_async`
- `job_status`
- `job_output`
- `job_cancel`
- `job_list`

`job_start` i `run_script_async` zwracają `session_id` bez czekania na koniec
procesu potomnego. Stan i zredagowany output są zapisywane, a zarejestrowane
ścieżki tymczasowe są czyszczone po zakończeniu lub anulowaniu.

Typowy lifecycle:

```text
job_start -> STARTED + session_id
job_status -> RUNNING
job_status -> COMPLETED | FAILED | CANCELLED | FINISHED_EXIT_UNKNOWN
job_output -> ograniczony stdout/stderr
```

## SSH, WSL i Docker

SSH może korzystać z systemowego SSH agenta i zaimportowanych profili hostów
bez wczytywania prywatnego materiału kluczy do zwykłego outputu MCP. Polityka
uprawnień jest stosowana osobno dla każdego skonfigurowanego hosta.

Na Windows narzędzia WSL zapewniają wykrywanie dystrybucji, translację ścieżek
i kontrolowane wykonanie komend w zainstalowanych dystrybucjach.

Narzędzia Docker udostępniają informacje o daemonie, wersji i kontenerach oraz
ograniczone operacje, gdy Docker jest dostępny. Docker jest opcjonalny; brak
działającego daemona nie blokuje niezależnych funkcji Local Ops.

## Automatyzacja przeglądarki

Automatyzacja przeglądarki korzysta z Playwright. Zaimplementowane są m.in.
lifecycle, karty i nawigacja, snapshoty i wyszukiwanie tekstu,
click/fill/type/press, select/scroll, upload/download, screenshoty, diagnostyka
console/network i narzędzia do recordingu. Binaria przeglądarki instaluje się
osobnym krokiem Playwright; zobacz [Windows](docs/windows.md) i
[instalację](docs/installation-pl.md).

## Automatyzacja pulpitu

Na Windows automatyzacja pulpitu używa `pywinauto`; zwalidowane środowisko
Windows korzysta także z `mss`, Pillow i wsparcia pywin32 instalowanego przez
odpowiednie extras.

Linux obsługuje operacje okien X11 przez narzędzia systemowe takie jak `wmctrl`
i `xdotool`, jeśli są dostępne. Na Wayland brak nieograniczonej globalnej
automatyzacji jest raportowany jako ograniczenie capability, zamiast udawać
powodzenie.

macOS używa `osascript`/System Events do zaimplementowanych operacji okien oraz
`screencapture` do screenshotów. Mogą być wymagane uprawnienia Accessibility,
Automation i Screen Recording. Końcowa natywna walidacja interaktywna nadal
pozostaje do wykonania.

## FFmpeg i multimedia

Narzędzia FFmpeg/ffprobe obejmują inspekcję, konwersję, trim, concat, resize,
zmianę FPS, ekstrakcję klatek/audio, dodawanie audio/subtitles oraz konwersję
GIF/video. Dłuższe operacje mają warianty async oparte o ten sam system jobs.
FFmpeg jest zależnością systemową i nie jest bundlowany z projektem.

Obsługa screen recordingu jest zaimplementowana. Interaktywny smoke test
recordingu na Windows pozostaje jednym z końcowych punktów walidacji prerelease.

## OBS (opcjonalny)

Integracja OBS korzysta z OBS WebSocket i jest opcjonalna. Zawiera krótkie
status calls oraz operacje scen, źródeł, nagrywania, streamingu i statystyk.
Normalny test suite nie wymaga uruchomionego OBS. Obecna walidacja Windows ma
status OBS `SKIPPED`, ponieważ OBS/WebSocket nie był uruchomiony; nie jest to
traktowane jako błąd Local Ops.

## Instalacja

Wymagany jest Python 3.11 lub nowszy. Installer świadomie odrzuca interpretery
Pythona dostarczane przez runtime'y lokalnych modeli i tworzy dedykowane
środowisko użytkownika dla Codex Local Ops.

### Windows — główna ścieżka

W katalogu repozytorium uruchom w PowerShell:

```powershell
.\setup.ps1
```

`setup.ps1` tworzy lub ponownie wykorzystuje dedykowany venv Local Ops,
instaluje bieżące źródła, tworzy konfigurację Local Ops, wykonuje backup
istniejącego `config.toml` Codex i rejestruje serwer STDIO `codexLocalOps`.

Dla zwalidowanego zestawu funkcji desktop/browser zainstaluj extras do tego
samego venv, będąc w katalogu repozytorium:

```powershell
$LocalOpsPython = Join-Path $env:USERPROFILE '.codex-local-ops.venv\Scripts\python.exe'
& $LocalOpsPython -m pip install --upgrade ".[desktop,browser,windows]"
& $LocalOpsPython -m playwright install chromium
```

Obsługa OBS jest opcjonalna:

```powershell
& $LocalOpsPython -m pip install --upgrade ".[obs]"
```

Następnie wykonaj First Run:

```powershell
clops manager
# lub
clops wizard
```

Szczegóły: [docs/installation-pl.md](docs/installation-pl.md) i
[docs/windows.md](docs/windows.md).

### Linux i macOS

W katalogu repozytorium:

```sh
./setup.sh
```

Linux jest platformą drugorzędną i wymaga dalszej native validation. macOS nie
jest jeszcze w pełni zwalidowany na fizycznym interaktywnym Macu. Przed
włączeniem funkcji desktopowych zobacz [docs/linux.md](docs/linux.md) i
[docs/macos.md](docs/macos.md).

## Aktualizacja

Zaktualizuj checkout źródeł, przejrzyj changelog i ponownie uruchom odpowiedni
setup script. Installer aktualizuje pakiet w istniejącym dedykowanym środowisku
i odświeża rejestrację MCP, wykonując backup konfiguracji Codex.

Windows:

```powershell
git pull
.\setup.ps1
```

Linux/macOS:

```sh
git pull
./setup.sh
```

Jeśli używasz extras, zaktualizuj je w tym samym venv po aktualizacji core.

## Odinstalowanie

Windows:

```powershell
.\uninstall.ps1
```

Domyślnie konfiguracja Local Ops zostaje zachowana. Użyj `-RemoveConfig` tylko,
gdy świadomie chcesz usunąć także konfigurację.

Linux/macOS:

```sh
./uninstall.sh
```

Argument `--remove-config` usuwa również katalog konfiguracji Local Ops.

## Troubleshooting

- [Ogólny troubleshooting](docs/troubleshooting.md)
- [Windows](docs/windows.md)
- [Linux](docs/linux.md)
- [macOS](docs/macos.md)
- [Polityka bezpieczeństwa](SECURITY.md)

Błędy widoczne wyłącznie wewnątrz Web/Native sandboxu, np. ograniczenia
uruchamiania procesów lub desktop capture, nie dowodzą awarii zwykłego backendu
interaktywnego. Problemy desktopowe należy odtworzyć w normalnej sesji
użytkownika przed zmianami w zainstalowanym środowisku.

## Development

Utwórz środowisko Python 3.11+ i zainstaluj:

```sh
python -m pip install -e ".[dev,desktop,browser,obs,windows]"
```

Extra `windows` ma marker platformy i nie instaluje pywinauto na Linux/macOS.
Zmiany powinny zachowywać strukturalne capability failures dla funkcji
opcjonalnych. Do repozytorium nie wolno dodawać lokalnych ścieżek, credentials,
profili przeglądarki, logów ani wygenerowanych artefaktów walidacyjnych.

Zobacz [CONTRIBUTING.md](CONTRIBUTING.md).

## Testy

Podstawowa walidacja lokalna:

```sh
python -m compileall -q src tests scripts
python -m pytest -m "not interactive"
python scripts/mcp_smoke.py
```

Interaktywna walidacja desktopowa jest oddzielona od headless CI. Na Windows
`scripts/validate_interactive_windows.ps1` testuje prawdziwą sesję desktopową i
zapisuje ignorowane artefakty lokalne w `artifacts/`.

GitHub Actions uruchamia compile, unit tests, STDIO MCP smoke, wykrywanie
platformy i walidację builda paczki na `windows-latest`, `ubuntu-latest` i
`macos-latest`. Nie zastępuje to prawdziwej interaktywnej walidacji pulpitu.

## Wersjonowanie i release

Projekt używa SemVer dla tagów Git i release. Planowany pierwszy publiczny tag
to `v0.1.0-beta.1`; odpowiada mu wersja pakietu Python `0.1.0b1` zgodna z
PEP 440. Workflow release buduje i waliduje artefakty wyłącznie po świadomym
pushu taga `v*` i nie publikuje automatycznie pakietu ani GitHub Release.

Zobacz [CHANGELOG.md](CHANGELOG.md) i
[docs/release-notes-template.md](docs/release-notes-template.md).

## Licencja

Codex Local Ops jest udostępniany na licencji [MIT](LICENSE).
