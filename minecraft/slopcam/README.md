# slopcam

The part of slopgen that lives inside Minecraft (Fabric 1.21.1). It lets the operator
mark up a world for machinima, and lets slopgen — connected over a localhost socket —
read that markup and, in later stages, stage, record and film scenes with Easy NPC
actors and Flashback.

## Build and install

```sh
nix-shell minecraft/slopcam/shell.nix --run 'gradle build'   # from the repo root
nix-shell --run '.venv/bin/python -m slopgen minecraft install --fandom ОБЪЕКТ'
```

`install` builds the mod (unless `--no-build`) and copies the jar into the mods folder
of the fandom's Prism instance (`[minecraft] instance` in `fandom.toml`), replacing an
older slopcam. It needs Flashback 0.39.8 and Easy NPC 7.12.1+ installed beside it.

## Markup mode

Entered and left with the **Markup** button in the pause menu — no hotkeys, so nothing
collides with Axiom. Inside it you fly like a spectator, through walls; the hotbar is
nine tools (wheel or 1–9); **E** opens the markup menu instead of the inventory.

| Tool | |
|---|---|
| Select | LMB select, RMB move a point/camera here, Del delete, Ctrl+Z undo |
| Box / Polygon / Brush | add pieces to one area; with Shift, cut them out of the selected one |
| Line | routes, queues, camera rails |
| Point | where an actor stands and which way they face |
| Camera | a fixed camera, as you look, with a snapshot |
| Properties | roles, name, description of a shape; on an NPC — make it a character's face |
| Check | an actor's ghost on a point |

The markup is written to `<save>/slopgen/registry.json` at once; slopgen reads the same
file (`slopgen minecraft places`). The game mode you entered with is kept in the save
and given back even if the game was closed while in the mode.

## The socket

`127.0.0.1:25599` (`config/slopcam.json`), newline-delimited JSON: `{"id", "method",
"params"}` → `{"id", "ok", "result" | "error"}`, plus unprompted `{"event", "data"}`.
Methods: `hello`, `status`, `registry.get`, `registry.put`, `markup.state`,
`world.scan`, `perform` (play one scene and record it), `perform.cancel`, `render`
(film recorded takes in one trip out of the world and back).

## Playing and filming

A take is played first and filmed second. `perform` makes the player an invisible
spectator above the place, sets time and weather, places the actors as copies of their
Easy NPC presets (AI off, moved by the mod), starts Flashback, runs the script a tick
at a time, and puts everything back; its report carries every actor's position on every
tick and a scan of the blocks around. `render` opens each replay in Flashback — which
closes the world, hence batches — writes the camera keys slopgen planned, hides the
spectators, the player and the name tags, exports (shaders on for a final) and reopens
the world. Calls into Flashback's internals are in `FlashbackBridge`, pinned to 0.39.8.
Units of Easy NPC's part rotations are a config switch (`poseRadians`, `positionScale`)
until confirmed in game.

---

# slopcam (RU)

Часть slopgen, которая живёт внутри Minecraft (Fabric 1.21.1). Даёт оператору
разметить мир для машинимы, а slopgen — подключившись через локальный сокет — читать
эту разметку и (на следующих этапах) ставить, записывать и снимать сцены с актёрами
Easy NPC и Flashback.

## Сборка и установка

```sh
nix-shell minecraft/slopcam/shell.nix --run 'gradle build'   # из корня репозитория
nix-shell --run '.venv/bin/python -m slopgen minecraft install --fandom ОБЪЕКТ'
```

`install` собирает мод (без `--no-build`) и кладёт jar в папку модов Prism-инстанса
фандома (`[minecraft] instance` в `fandom.toml`), заменяя старый slopcam. Рядом нужны
Flashback 0.39.8 и Easy NPC 7.12.1+.

## Режим разметки

Вход и выход — кнопка **«Разметка»** в меню паузы; горячих клавиш нет, с Axiom ничего не
конфликтует. Внутри летаешь как наблюдатель, сквозь стены; хотбар — девять инструментов
(колесо или 1–9); **E** открывает меню разметки вместо инвентаря.

| Инструмент | |
|---|---|
| Выбор | ЛКМ — выбрать, ПКМ — перенести точку/камеру сюда, Del — удалить, Ctrl+Z — отменить |
| Коробка / Многоугольник / Кисть | добавляют куски в одну зону; с Shift — вырезают из выбранной |
| Линия | маршруты, очереди, рельсы камер |
| Точка | где стоит актёр и куда смотрит |
| Камера | стационарная камера — как смотришь ты, со снимком |
| Свойства | роли, имя, описание фигуры; по NPC — сделать его подвидом персонажа |
| Проверка | призрак актёра на точке |

Разметка сразу пишется в `<сейв>/slopgen/registry.json`; slopgen читает тот же файл
(`slopgen minecraft places`). Режим игры, с которым ты вошёл, хранится в сейве и
возвращается, даже если игру закрыли прямо в режиме разметки.

## Сокет

`127.0.0.1:25599` (`config/slopcam.json`), JSON построчно: `{"id", "method", "params"}`
→ `{"id", "ok", "result" | "error"}`, плюс события `{"event", "data"}`. Методы: `hello`,
`status`, `registry.get`, `registry.put`, `markup.state`, `world.scan`, `perform` (сыграть
одну сцену и записать), `perform.cancel`, `render` (снять записанные дубли за одну
поездку из мира и обратно).

## Игра и съёмка

Дубль сначала играется, потом снимается. `perform` делает игрока невидимым наблюдателем
над местом, ставит время и погоду, расставляет актёров — копии их пресетов Easy NPC (ИИ
выключен, двигает мод), запускает Flashback, проигрывает сценарий по тикам и всё
возвращает; в отчёте — позиции всех актёров на каждом тике и снимок блоков вокруг.
`render` открывает каждый реплей во Flashback — а это закрывает мир, поэтому пакетами, —
пишет ключи камеры, рассчитанные slopgen, прячет наблюдателей, игрока и подписи над
головами, экспортирует (для чистовика — с шейдерами) и снова открывает мир. Вызовы во
внутренности Flashback собраны в `FlashbackBridge` и привязаны к 0.39.8. Единицы
поворотов частей тела Easy NPC — переключатели в конфиге (`poseRadians`,
`positionScale`), пока не подтверждены в игре.
