# cli

**EN:** Typer-based CLI. `slopgen` with no arguments launches the TUI. A **mode** comes first and shapes the rest of the line: `slopgen info <lang> <type> [flags]` for the minute-of-info clip, `slopgen drama <lang> [flags]` for the AI drama. Parameter resolution order (info): CLI flags > preset > account defaults > global defaults; drama builds its parameters from its own flags.

Two commands reopen a run that parked itself, each straight into the screen it is waiting on: `slopgen gather [dir]` for hand-made clips, `slopgen review [dir]` for a breakpoint — omit the directory and the latest such run is found. `--resume <dir>` continues a crashed one. Also here: the `--list-*` inspectors and the console progress printer, which prints the exact command to type when a run ends parked or failed.

**RU:** CLI на Typer. `slopgen` без аргументов запускает TUI. Первым идёт **режим**, он задаёт вид остальной команды: `slopgen info <язык> <тип> [флаги]` — ролик-минутка, `slopgen drama <язык> [флаги]` — ИИ-дорама. Приоритет параметров (info): флаги CLI > пресет > дефолты аккаунта > глобальные; дорама собирает параметры из своих флагов.

Две команды возвращают к застывшему прогону, сразу на тот экран, которого он ждёт: `slopgen gather [папка]` — ручные клипы, `slopgen review [папка]` — брейкпоинт; без папки берётся последний такой прогон. `--resume <папка>` продолжает оборвавшийся. Здесь же инспекторы `--list-*` и консольный принтер прогресса, который печатает готовую команду, если прогон встал или упал.
