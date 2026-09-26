# Setup Assistant

Setup Assistant jest warstwą lifecycle i połączeń nad istniejącym runtime Codex
Local Ops. Osobno śledzi zdrowie stabilnego runtime, rejestrację Codexa, trusted
roots, managed instructions oraz integrację ChatGPT Web.

## Model statusów

Kroki używają:

- `PASS` — bezpośrednio zweryfikowane;
- `WARNING` — są użyteczne dane, ale potrzebna jest uwaga;
- `FAIL` — potwierdzona awaria blokuje krok;
- `NOT_CONFIGURED` — komponent nie jest skonfigurowany;
- `WAITING_FOR_USER` — potrzebna jest akcja w koncie/UI, autoryzacja albo ręczny
  dowód.

`READY` pojawia się wyłącznie dla kompletnego łańcucha Web → Windows po
zweryfikowaniu wszystkich wymaganych hopów jako `PASS`.

## Zdrowie lokalne i Web są niezależne

Ścieżka lokalna:

```text
Codex → codexLocalOps → host Windows
```

Ścieżka Web dodaje:

```text
ChatGPT Web → Codex Native2 → Full Harness → Codex
```

Awaria Web nie oznacza potrzeby reinstalacji Local Ops. Setup Assistant pokazuje
konkretną uszkodzoną warstwę i pozostawia zdrowy stabilny runtime bez zmian.

## Komendy

```powershell
clops setup-assistant web-status
clops setup-assistant web-plan
clops setup-assistant web-repair
clops setup-assistant web-verify
```

`web-status`, `web-plan` i `web-repair` są niedestrukcyjne. Odczytują tylko
bezpieczne dane setupu. `web-verify --confirm <HOP>` zapisuje wynik już
zakończonej weryfikacji w stanie Setup Assistant; nie zmienia profilu launchera
ani konta ChatGPT.

Dozwolone hop names:

```text
FULL_HARNESS_TO_CODEX
CODEX_TO_CODEXLOCALOPS
WINDOWS_HOST_VISIBLE_THROUGH_LOCALOPS
```

## Managed AGENTS.md

Managed block zachowuje cały tekst użytkownika poza markerami. Wersja 2 dodaje:

- preferowanie `codexLocalOps` dla prawdziwej pracy na Windows;
- priorytet aktualnie otwartego workspace;
- zakaz tworzenia katalogów z nazw-placeholderów;
- błąd sandboxu Web nie oznacza awarii hosta;
- zakaz naprawiania zdrowego Local Ops tylko dlatego, że Web nie uruchamia host
  Pythona;
- poprawną ścieżkę dev `%USERPROFILE%\.codex-local-ops.venv\Scripts\python.exe`;
- zakaz obchodzenia jawnej odmowy autoryzacji innym executorem;
- zachowanie trwałych async jobs i sprawdzanie tego samego joba po disconnect;
- zachowanie zwykłej granicy autoryzacji dla zmian publicznych/zdalnych.

## Repair

Naprawa lokalnego runtime pozostaje komponentowa. Problemy ChatGPT Web mają
`repair_scope: chatgpt-web` i wskazują dokładny niedokończony krok. Plan repair
Web nie tworzy staging runtime Local Ops i go nie reinstaluje.

## Granica bezpieczeństwa

Setup Assistant nie zapisuje danych logowania ChatGPT. Parser stanu launchera
przepuszcza tylko białą listę pól setupu, a nieznane pola odrzuca. Ręczna
weryfikacja połączenia zapisuje wyłącznie dozwoloną nazwę hopu, status i czas.

Jawne odmowy `AUTHORIZATION_REQUIRED`, `PERMISSION_REQUIRED`,
`APPROVAL_REQUIRED`, `SECURITY_DENIED` i `POLICY_DENIED` stają się
`WAITING_FOR_USER`; tego samego side effect nie wolno ponawiać innym executorem.
