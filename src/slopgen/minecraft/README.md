# minecraft — machinima

A fandom video's picture can be **played** in the world's own Minecraft build and
filmed under the very words being said: a nod lands on «кивнула». Such a piece is a
**machinima block** — a kind of frame on the picture track, never a card, never filed
into any frame base. Inside the game the `slopcam` mod (`minecraft/slopcam/`) does the
markup, the playing and the filming; this package is slopgen's half.

## The pieces

| Module | What it is |
|---|---|
| `registry` | The world's markup, as the mod writes it to `<save>/slopgen/registry.json`: places (groups, no geometry), and in them areas (pieces that add and cut), points, lines, cameras — each with roles — plus characters with their faces (Easy NPC presets) and props. |
| `direction` | A block's direction file: scenes → cast, crowds, actions, shots; every key anchored to a word. `validate` names what is wrong and lists what would be right. |
| `anchors` | Words ↔ seconds on the picture track's own clock; catches a rewritten line. |
| `library` | Poses (held) and gestures (played) as per-part rotation keys with per-transition interpolation (`easing`). |
| `crowd` | Density and shares of characters and faces → who stands where, neighbours different. |
| `compile` | Direction → one script per scene for the mod: ticks, coordinates, dense body tracks; dialogue and crowds expanded. |
| `camera` | The camera search: a view ordered, obstructions flown around or closer to, never a dropped shot. |
| `film` | Play a scene (`perform`), plan its camera on the report, render replays in one batch, join. |
| `direct` | The LLM director: the world told in words, the narration word by word with seconds; one repair round. |
| `block` | Blocks on a job's track: claimed by the matcher, measured from words, shot; `MachinimaWaiting` when the game is closed. |
| `client`, `markup`, `paths` | The socket to the game; the markup read and written through the game or the save; where the instance and save are. |

## Using it

1. Tell the fandom where it is built — the world's Minecraft tab in the browser, or
   `[minecraft] instance = "Object"`, `world = "ОБЪЕКТ"` in its `fandom.toml`.
2. `slopgen minecraft install --fandom ОБЪЕКТ`, start the game, open the world.
3. Mark it up in game: Esc → **Markup** (see `minecraft/slopcam/README.md`).
4. Tick `minecraft` among the sources of a fandom run — or make any shot a block in the
   montage room — and direct, draft and compile it there.

`slopgen minecraft status|places|shoot` check the game, print the markup, and run the
whole path on one direction file without a run.

---

# minecraft — машинима

Картинку фандомного ролика можно **сыграть** в майнкрафтовой постройке самого мира и
снять под те самые слова, что звучат: кивок ложится на «кивнула». Такой кусок —
**блок машинимы**: вид кадра на дорожке, а не карточка, и ни в какую базу кадров он не
попадает. Внутри игры разметку, игру и съёмку делает мод `slopcam`
(`minecraft/slopcam/`); этот пакет — половина slopgen.

## Из чего состоит

| Модуль | Что это |
|---|---|
| `registry` | Разметка мира, как мод пишет её в `<сейв>/slopgen/registry.json`: места (группы без геометрии), в них зоны (куски, которые добавляют и вырезают), точки, линии, камеры — с ролями; персонажи с подвидами (пресеты Easy NPC) и реквизит. |
| `direction` | Файл режиссуры блока: сцены → актёры, массовка, действия, планы; каждый ключ привязан к слову. `validate` говорит, что не так, и перечисляет, что было бы правильно. |
| `anchors` | Слова ↔ секунды на часах дорожки кадров; ловит переписанную реплику. |
| `library` | Позы (держатся) и жесты (играются) — ключи поворотов частей тела, у каждого перехода своя интерполяция (`easing`). |
| `crowd` | Плотность и доли персонажей и подвидов → кто где стоит, соседи разные. |
| `compile` | Режиссура → сценарий на каждую сцену для мода: тики, координаты, плотные дорожки тела; диалог и массовка развёрнуты. |
| `camera` | Поиск пути камеры: ракурс заказан, препятствие облетается или камера подлетает ближе — план не отбрасывается никогда. |
| `film` | Сыграть сцену (`perform`), рассчитать камеру по отчёту, отрендерить реплеи пакетом, склеить. |
| `direct` | LLM-режиссёр: мир словами, озвучка по словам с секундами; один круг починки. |
| `block` | Блоки на дорожке задания: заявлены подборщиком, измерены по словам, сняты; без игры — `MachinimaWaiting`. |
| `client`, `markup`, `paths` | Сокет к игре; разметка через игру или сейв; где инстанс и сейв. |

## Как пользоваться

1. Скажи фандому, где он построен — вкладка «Майнкрафт» у мира в браузере или
   `[minecraft] instance = "Object"`, `world = "ОБЪЕКТ"` в его `fandom.toml`.
2. `slopgen minecraft install --fandom ОБЪЕКТ`, запусти игру, открой мир.
3. Разметь мир в игре: Esc → **«Разметка»** (см. `minecraft/slopcam/README.md`).
4. Отметь `minecraft` среди источников фандомного прогона — или сделай любой отрезок
   блоком в монтажке — и там ставь, смотри черновик и компилируй.

`slopgen minecraft status|places|shoot` проверяют игру, печатают разметку и прогоняют
весь путь на одном файле режиссуры без прогона.
