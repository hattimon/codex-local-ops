# Konfiguracja ChatGPT Web na Windows

Ten przewodnik łączy działający Codex Local Ops z aktualnym launcherem
`miuuyy/codex-chatgpt-web` oraz Full Harness w ChatGPT.

## Zwalidowana baza upstream

Integracja została sprawdzona względem `codex-chatgpt-web` **v6.1.1** z
26.09.2026. Aktualny instalator Windows używa oficjalnego assetu release i
oficjalnego `install-launcher.ps1`. Skrypt pobiera EXE oraz `checksums.txt`,
weryfikuje SHA-256, instaluje per-user i zachowuje ustawienia launchera oraz
profil ChatGPT podczas aktualizacji.

Oficjalna komenda upstream:

```powershell
irm https://github.com/miuuyy/codex-chatgpt-web/releases/latest/download/install-launcher.ps1 | iex
```

Przed aktualizacją zamknij **Codex Web GPT**. Nie odinstalowuj zdrowego launchera
i nie usuwaj jego profilu ChatGPT.

## Prowadzona konfiguracja

1. Uruchom **Codex Web GPT**.
2. Zaloguj się do ChatGPT w osadzonej przeglądarce launchera. Codex Local Ops nie
   prosi o dane logowania, nie odczytuje ani nie zapisuje cookies, browser
   storage, tokenów, nagłówków autoryzacyjnych ani plików profilu.
3. Uruchom browser smoke test.
4. Wybierz **Install models** / **Install into Codex**.
5. Zamknij cały Codex razem z procesami w tle i uruchom go ponownie przy
   działającym launcherze. Poczekaj na potwierdzenie katalogu modeli.
6. Otwórz **MCP**, utwórz wymagany OpenAI Tunnel i zwykły API key dla tunelu.
7. Wybierz **Connect harness**.
8. W ChatGPT włącz **Developer Mode**.
9. Utwórz **nowy** connector o dokładnej nazwie **Codex Native2**.
10. Wybierz ten sam Tunnel, ustaw **Authentication: None**, a w Permissions
    wybierz **Allow all actions**.
11. W launcherze uruchom **Verify runtime**.
12. Wykonaj jeden read-only test end-to-end przez Native2, który zwróci dane o
    hoście Windows przez `codexLocalOps`.

Nie zmieniaj nazwy starego `Codex Native` i nie używaj go ponownie. Aktualny
upstream wymaga nowej tożsamości connectora, aby ChatGPT wczytał bieżący kontrakt
MCP.

## Status Setup Assistant

```powershell
clops setup-assistant web-status
```

Status pokazuje osobno:

- `CODEX_WEB_GPT`
- `CHATGPT_LOGIN`
- `BROWSER_SMOKE_TEST`
- `WEB_MODELS`
- `FULL_HARNESS`
- `CODEX_NATIVE2`
- `VERIFY_RUNTIME`
- `FULL_HARNESS_TO_CODEX`
- `CODEX_TO_CODEXLOCALOPS`
- `WINDOWS_HOST_VISIBLE_THROUGH_LOCALOPS`

Końcowy `READY` pojawia się dopiero po faktycznej weryfikacji wszystkich hopów.
Kroki wymagające konta/UI pozostają `WAITING_FOR_USER` zamiast być zgadywane jako
`PASS`.

Local Ops czyta z `launcher-state.json` wyłącznie niesekretne pola statusu:
browser smoke, instalację modeli/katalog, wymagany restart oraz weryfikację MCP.
Nieznane pola są ignorowane, więc cookies i tokeny nie trafiają do stanu ani
outputu Local Ops.

## Plan instalacji lub aktualizacji

```powershell
clops setup-assistant web-plan
```

Plan zwraca `INSTALL`, `UPDATE` albo `NONE` i wskazuje oficjalny instalator. Nie
uruchamia po cichu pobranego pliku. Aktualizacja zachowuje konfigurację launchera
i profil ChatGPT oraz nie zaleca odinstalowania zdrowej instalacji.

## Repair

```powershell
clops setup-assistant web-repair
```

Problemy Web są oddzielone od zdrowia runtime Local Ops. Wykrywane są m.in.:

- brak launchera lub brak potwierdzenia aktualnej wersji;
- niezweryfikowany lub stary browser smoke po aktualizacji;
- modele zainstalowane, ale restart/katalog Codexa niepotwierdzony;
- niedokończony Full Harness;
- niedokończony `Codex Native2` / Verify Runtime;
- brak końcowego dowodu Web → Windows.

Uszkodzona integracja Web nie powoduje reinstalacji Local Ops. Zdrowy
`codexLocalOps` pozostaje raportowany jako zdrowy.

## Błędy Web i autoryzacja

`stream disconnected`, przejściowy błąd transportu i nieudany Web turn nie są
dowodem awarii Windows, Pythona, Dockera, SSH ani Local Ops.

Jeśli Web/Native2 nie potrafi uruchomić znanego interpretera dev, poprawna
ścieżka to:

```text
%USERPROFILE%\.codex-local-ops.venv\Scripts\python.exe
```

Nie umieszczaj zagnieżdżonego `.venv` w `%USERPROFILE%\.codex-local-ops`; dev
venv jest sąsiednim katalogiem `.codex-local-ops.venv`. Nie szukaj innego
Pythona jako obejścia ograniczenia harnessu.

Jeżeli Local Ops zwróci `AUTHORIZATION_REQUIRED`, `PERMISSION_REQUIRED`,
`APPROVAL_REQUIRED`, `SECURITY_DENIED` albo `POLICY_DENIED`, ta operacja kończy
się jako `WAITING_FOR_USER`. Nie wykonuj tego samego side effect przez Codex
exec, PowerShell, cmd, Python, WSL, Docker, SSH, browser automation ani inne MCP.

## Diagnostyka launchera

Dla problemów launchera użyj **Settings → Run doctor**, odtwórz problem jeden
raz i wybierz **Activity → Export safe log**. Nie eksportuj cookies, browser
storage, nagłówków autoryzacyjnych ani surowego profilu launchera.

Dla `Reconnecting`, `stream disconnected` lub `ChatGPT failed` sprawdzaj końcowy
szczegółowy błąd. Sam reconnect albo ogólny 502 nie wskazuje uszkodzonego hopu.
