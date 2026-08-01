# pipeline

**EN:** The conveyor.

`job.py` — `VideoJob`/`Scene`/`BgAsset`/`FgInsert`/`Word`, the state passed between stages. `context.py` — `AppContext`: resolved configs (visuals profile, cast, orchestration, active LLM profile), the LLM client, topic history, and an optional `progress()` sink a stage calls from inside its own loop. `orchestrator.py` — runs a stage chain per video and reports through `on_event` (consumed by both CLI and TUI); one failed video never kills the batch.

Two chains, sharing stage NAMES so checkpoints stay uniform: **info** is idea (skipped when `--idea` was given) → script → tts → footage → subtitles → assemble → metadata; **drama** drops idea and swaps in `drama_script` / `drama_footage`. `drama.py` plans the shot list — `plan_slots` expands the orchestration into one slot per clip, each pinned to the generator that will make it; `parts.py` splits a drama into cliffhanger parts.

Three ways a run can stop and be picked up again, all on the same checkpoint:
* `checkpoint.py` — every finished stage is written to `<run>/checkpoint.json`, so `--resume` skips what is already on disk and continues from the failure.
* `manual.py` — the user-assisted (`manual`) generator: slopgen writes a shotlist and prompts, the operator makes the clips by hand in an external tool, and the job parks as `paused` until they are all delivered (`slopgen gather`).
* `review.py` — breakpoints: after a chosen stage the job parks as `review` and its output is exposed as an editable document (`Doc` of typed `Row`s grouped per scene), folded back by `apply`, which also reports whether the edit made that stage's own output stale (`slopgen review`).

**RU:** Конвейер.

`job.py` — `VideoJob`/`Scene`/`BgAsset`/`FgInsert`/`Word`, состояние, идущее между стадиями. `context.py` — `AppContext`: резолвнутые конфиги (профиль видеоряда, каст, оркестрация, активный LLM-профиль), клиент LLM, история тем и необязательный приёмник `progress()`, который стадия дёргает изнутри своего цикла. `orchestrator.py` — гонит цепочку стадий по каждому ролику и докладывает через `on_event` (слушают и CLI, и TUI); падение одного ролика не убивает батч.

Цепочки две, с общими ИМЕНАМИ стадий, чтобы чекпойнты были единообразны: **info** — idea (пропускается при `--idea`) → script → tts → footage → subtitles → assemble → metadata; **drama** выбрасывает idea и подставляет `drama_script` / `drama_footage`. `drama.py` планирует список кадров — `plan_slots` разворачивает оркестрацию в слоты по клипу, каждый закреплён за своим генератором; `parts.py` режет дораму на части с клиффхэнгерами.

Прогон может встать и быть продолжен тремя способами, все на одном чекпойнте:
* `checkpoint.py` — каждая законченная стадия пишется в `<прогон>/checkpoint.json`, поэтому `--resume` пропускает готовое и продолжает с места падения.
* `manual.py` — user-assisted генератор (`manual`): slopgen пишет шотлист и промпты, оператор делает клипы руками во внешнем сервисе, а задача стоит в `paused`, пока не принесут все (`slopgen gather`).
* `review.py` — брейкпоинты: после выбранной стадии задача встаёт в `review`, а её результат выдаётся редактируемым документом (`Doc` из типизированных `Row`, сгруппированных по сценам), который `apply` складывает обратно и заодно сообщает, устарел ли от правки результат самой стадии (`slopgen review`).
