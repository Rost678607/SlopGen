# slopgen

Industrial-scale short-form video factory: **idea → script → TTS voiceover → stock/AI footage → ffmpeg assembly with subtitles → metadata → publish**. Fully automated, config-driven, with a TUI for humans and a CLI for cron.

*Русская версия — [ниже](#slopgen-ru).*

---

## Requirements

- **Python 3.12+**
- **ffmpeg** on your `PATH` (the assembly engine)
- Internet access (edge-tts, stock/AI APIs, your LLM provider, YouTube)

Install ffmpeg: `winget install Gyan.FFmpeg` (Windows) · `brew install ffmpeg` (macOS) · `sudo apt install ffmpeg` (Debian/Ubuntu) · `sudo pacman -S ffmpeg` (Arch).

## Install

Works on Linux, macOS, and Windows. Create a virtualenv and install:

```bash
python -m venv .venv
# Linux/macOS:
source .venv/bin/activate
# Windows (PowerShell):
# .venv\Scripts\Activate.ps1

pip install -r requirements.txt
pip install -e .
cp .env.example .env            # fill in your keys (Windows: copy .env.example .env)
```

<details>
<summary>Nix / NixOS</summary>

The repo ships a `shell.nix` with Python 3.12, ffmpeg, and DejaVu fonts:

```bash
nix-shell                       # creates and activates .venv on first entry
pip install -r requirements.txt && pip install -e .
```

</details>

`.env` keys:

| Key                  | Needed for                                      | Where to get          |
| -------------------- | ----------------------------------------------- | --------------------- |
| `DEEPSEEK_API_KEY`   | LLM (provider `deepseek`)                       | platform.deepseek.com |
| `GEMINI_API_KEY`     | LLM (provider `gemini`, has a free tier)        | aistudio.google.com   |
| `OPENROUTER_API_KEY` | LLM (provider `openrouter`, has `:free` models) | openrouter.ai         |
| `PEXELS_API_KEY`     | stock footage (primary)                         | pexels.com/api        |
| `PIXABAY_API_KEY`    | stock footage (fallback)                        | pixabay.com/api/docs  |

Only the key for the provider selected in `configs/slopgen.toml` `[llm]` (or TUI → Configuration → LLM) is required. Everything else is key-free: edge-tts needs no key, YouTube uses OAuth (a client JSON, not an API key). Footage can stay key-free too via the `local` provider (`assets/footage/`).

## Quick start

```bash
# interactive: pick everything in the TUI, press START, walk away
slopgen

# headless: a MODE comes first, then its arguments
slopgen info en cyber                                     # minute-of-info clip
slopgen info ru story --ad example_vpn --ad-mode both --push yt_main -n 5
slopgen --preset daily_en                                 # a preset is an info run

# AI drama: a narrated story with a recurring cast + AI-generated shots
slopgen drama ru --scenario "Две подруги ссорятся из-за тайны" \
                 --cast example --duration-min 2 --tol 20 --parts 3
slopgen drama en --orchestration my_chain --ad example_vpn --dry-run

# generate without publishing (demo assets included)
slopgen info en cyber --ad example_vpn --dry-run
```

Single-part output lands in `output/<timestamp>_<type|mode>_<lang>/<n>/final.mp4` + `metadata.json`.
Multi-part dramas produce `part_01.mp4`, `part_02.mp4`, ... together in that same `<n>/` directory.

### CLI reference

The first positional argument is the **mode**: `info` (the minute-of-info clip) or
`drama` (the AI web drama). Each mode shapes the rest of the line. Running
`slopgen` with no mode opens the TUI.

**`info LANG TYPE [flags]`**

| Argument / flag  | Meaning                                                                                                                 |
| ---------------- | ----------------------------------------------------------------------------------------------------------------------- |
| `LANG` `TYPE`    | positional: language (`en`/`ru`) and content type (`story`/`cyber`/`psych`/`facts`)                                     |
| `--idea "..."`   | your own topic; omit to let the LLM invent one                                                                          |
| `--visuals NAME` | visuals profile from `configs/visuals/` (default `classic`)                                                             |
| `--duration N`   | target spoken length in seconds (default 45; >60 is fine, Shorts allow up to 3 min). A hint for the LLM, not a hard cap |
| `--profanity N`  | swearing level in the narration, 0 (clean) … 100 (constant); in the TUI it's a slider on the Content step               |
| `--ad NAME`      | ad contract from `configs/ads/`                                                                                         |
| `--ad-mode`      | `overlay` (corner animation + caption), `native` (spoken mention + ad clip), `both`                                     |
| `--push NAME`    | account from `configs/accounts/`; omit → save locally                                                                   |
| `-n, --count N`  | videos per run                                                                                                          |
| `--preset NAME`  | load a parameter bundle from `configs/presets/`                                                                         |
| `--resume DIR`   | continue a crashed run from its output dir (the folder holding `checkpoint.json`)                                       |
| `--subs`         | subtitle style: `word_pop` / `phrases` / `karaoke`                                                                      |
| `--out DIR`      | output dir override                                                                                                     |
| `--dry-run`      | generate but don't publish (dev tool; picking "save locally" does the same)                                             |
| `--keep-temp`    | keep intermediate ffmpeg files                                                                                          |

**`drama LANG [flags]`** — shares `--ad`, `--ad-mode`, `--profanity`, `--push`, `-n/--count`, `--subs`, `--out`, `--dry-run`, `--keep-temp` with `info`, plus:

| Flag                   | Meaning                                                                        |
| ---------------------- | ------------------------------------------------------------------------------ |
| `LANG`                 | positional: narration language (`en`/`ru`)                                     |
| `--scenario "..."`     | the plot/premise; omit to let the LLM invent one                               |
| `--cast A,B`           | comma-separated character names from `configs/characters/`                     |
| `--orchestration NAME` | AI-generator chain from `configs/orchestration/` (default: one `wan2.1` stage) |
| `--duration-min N`     | target length in **minutes**                                                   |
| `--tol N`              | how many **seconds** the finished video may run over/under the target          |
| `--parts N`            | split one drama into N publishable parts; script cuts are planned as cliffhangers |
| `--voice ID`           | edge-tts narrator voice (default per language)                                 |

**Global** (before the mode, or standalone): `--resume DIR`, and the inspectors
`--list-types` `--list-ads` `--list-accounts` `--list-presets` `--list-visuals`
`--list-characters` `--list-orchestrations`.

Parameter priority (info mode): **CLI flags > preset > account defaults > global defaults**. An account config can carry its own default language/type/ad, so `slopgen info --push yt_main` alone is a valid command. Drama builds its parameters directly from its own flags (no preset/account merge yet).

**Crash recovery.** Every run is checkpointed to `<out>/<stamp>_<type>_<lang>/checkpoint.json` after each pipeline stage. If a run dies partway (network drop, killed process), the finished stages' outputs (TTS audio, downloaded footage, the job state) are kept, and the failing stage + error are recorded. Re-run with `slopgen --resume <that dir>` to skip the completed stages and continue from the point of failure — already-finished videos are left untouched, unfinished ones pick up where they stopped. When a run ends with failures, the summary prints the exact `--resume` command to use.

## TUI

`slopgen` with no arguments. Custom **Minecraft theme**, no footer — the top bar holds the RU/EN interface-language toggle, the `<-` back button and the command Palette.

- **Home** — centered menu, arrow keys + Enter.
- **Generate** — first pick a mode (**minute-of-info** or **AI drama**), then a step-by-step wizard with a vertical step list on the left. *Info:* 1) content (language, type, your own idea, a profanity slider), 2) visuals (profile + full overrides: background source/linkage/interval/Ken Burns, foreground inserts; target duration), 3) ads (a saved contract *or* fully manual fields), 4) publishing (account, count, subtitles), 5) summary with the equivalent CLI command and the GENERATE button. *Drama* adds a **Story** step (plot + a reorderable cast, edited on the right, with photo→appearance vision and AI cast-fill), adds a parts field to **Publishing** for cliffhanger splits, and turns the Visuals step into **orchestration** (an ordered list of AI generators; see below). Set everything up, press it, walk away.
- **Configuration** — sections on the left: LLM profiles (profile tabs, per-provider model presets, API-key input auto-saved to `.env`, ★ activation), ad contracts, accounts, presets. Entity sections have a tab per existing config file on top plus `+ new`; forms are prefilled, with 💾 save and 🗑 delete (confirmed).
- The chosen color theme persists across runs (`[ui].theme`).

## AI drama (`configs/characters/`, `configs/orchestration/`)

A second mode: a **narrated web drama** — one voiceover narrator tells a story (and may quote characters' lines inline) over AI-generated shots featuring a recurring cast.

- **Cast** (`configs/characters/*.toml`): `name`, `age`, `appearance`. Before generation each character is compiled once into a token-dense English `visual_prompt` that is injected into every shot the character appears in, so the look stays consistent (a text-only anchor — free generators won't lock a face perfectly). In the TUI you can build an ad-hoc cast, pull members from the library, upload a photo (vision → appearance), and let the AI fill the whole cast from the premise.
- **Orchestration** (`configs/orchestration/*.toml`): an ordered list of AI generators, each a `model` (`wan2.1`/`ltx-video`/`animatediff` video, `flux`/`turbo` image), a `key_mode` (`rotate` keys on a limit / `single` key then skip), and a `metric`+`amount`. The pipeline walks the stages in order and each makes its share of the clips: `percent` = a share of the length budget, `seconds`/`clips` = an absolute chunk, and the last stage fills the remainder. Multiple API keys (one per line in `.env`) are rotated across stages.
- **Length, parts & sync**: authored in **minutes** + a **tolerance** in seconds (the story may run a bit over/under). If parts >1, the writer labels scenes by part and places each non-final cut on a cliffhanger. Each scene gets one clip; the narration is synthesized per scene and time-stretched (ffmpeg `atempo`) to fit its clip, with subtitle timings rescaled to match — audio and video stay locked. A native ad, when enabled, is woven into the plot at the script level rather than bolted on.

Run it from the TUI (Generate → AI drama) or headless: `slopgen drama ru --scenario "…" --cast example --duration-min 2 --tol 20 --parts 3 --orchestration my_chain`.

## Visuals profiles (`configs/visuals/`)

The video track is a layered composition, configured per profile:

- **Background**: `stock_video` / `stock_photo` / `local_video` / `local_photo` / `ai_photo` / `ai_video` (free keyless generation — Pollinations images, Wan video via HF Spaces). Linkage `narration` = the LLM emits a photo/footage query for every ~N seconds of speech, tied to what is being said at that moment (Switzerland → the capital, a couple → a couple, a puppy → a puppy); `neutral` = random/looping content (e.g. gameplay). Photo backgrounds get Ken Burns motion (`none`/`subtle`/`strong`) and change every `interval_s`.
- **Foreground**: optional framed picture/clip inserts that are *event-driven*, not on a timer — the LLM decides which spoken phrases deserve an illustration, and each insert appears exactly while that phrase is spoken (timed from edge-tts word timings) and disappears afterwards. You only pick the source, width and position.

Shipped profiles: `classic` (stock video b-roll, the default), `slideshow` (narration-synced Ken Burns photos), `gameplay` (drop your minecraft-parkour/subway-surfers clips into `assets/footage/gameplay/`, narration photo inserts pop in front). In the TUI wizard the Visuals step prefills from a profile and any edited field turns the run into a custom profile.

## LLM profiles (`configs/llm/`)

Named connections: `provider` (`deepseek`/`gemini`/`openrouter`/`custom`), `model`, `base_url`, `temperature`, `web_search`. The active one is chosen by `[llm].profile` in `slopgen.toml`. API keys never live in TOML — they are env variables in `.env`; the TUI Configuration → LLM section lets you pick model presets per provider, paste the key (saved to `.env` automatically), toggle web search, activate and delete profiles.

**Web search** (`web_search = true`): gives the model a real `web_search` tool via standard OpenAI function calling. Before writing the script the model calls it, slopgen runs a keyless DuckDuckGo search and feeds the results back, so the narration is grounded in real, verified facts instead of invented names/events. Works on any provider whose model supports tool use (OpenAI, DeepSeek, OpenRouter, Gemini's compat endpoint); a model without tool calling will simply not use it.

Stock-footage API keys (Pexels, Pixabay) can also be pasted in the TUI under **Configuration → Footage API keys** — they are saved to `.env`. They're only needed for `stock_*` visuals; local assets need none.

## Configs (`configs/`)

Everything is hand-editable TOML; a new file in the folder = a new entity, no code changes.

- `slopgen.toml` — global: video size/fps, target duration, subtitle style/font/colors, music volume, active LLM profile, footage provider order, UI language/theme, defaults.
- `content/*.toml` — content types: per-language creative briefs (`idea_brief`, `script_brief`), edge-tts `voices`, `fallback_keywords` for stock search.
- `ads/*.toml` — ad contracts: `url`, overlay section (assets dir, caption `text`, `position`, `start_s`, `duration_s`, `width`), native section (assets dir, `talking_points` the LLM weaves into the script), description `snippet` (`{url}` is substituted).
- `accounts/*.toml` — publishing targets: `platform`, YouTube OAuth paths/privacy/category, optional `defaults` (lang/type/ad).
- `presets/*.toml` — full parameter bundles for one-command runs.
- `characters/*.toml` — AI-drama cast members (`name`, `age`, `appearance`, compiled `visual_prompt`).
- `orchestration/*.toml` — AI-drama generator chains (ordered `[[stages]]` with `model`/`key_mode`/`metric`/`amount`).

## Assets (`assets/`)

Drop files in, reference from configs:

```
assets/
  ads/<contract>/overlay/   # corner animations: .webm (alpha), .gif, .png
  ads/<contract>/native/    # pre-made ad video inserts
  music/                    # background tracks (one is picked at random, mixed at low volume)
  fonts/                    # extra subtitle fonts (passed to libass via fontsdir)
  footage/                  # local clips for the "local" footage provider
  footage/gameplay/         # background loops for the "gameplay" visuals profile
  images/                   # local pictures for photo backgrounds / foreground inserts
```

**Bring your own content.** `assets/music/`, `assets/footage/`, `assets/ads/` and the personal `configs/` (`characters/`, `ads/*.toml` except the example, `accounts/`) are git-ignored on purpose — drop your own (copyright-cleared) tracks, clips and cast in. The repo ships only neutral templates: `configs/characters/example.toml`, `configs/ads/example_vpn.toml`, and a few demo images.

Subtitles default to the **DejaVu Sans** font. It's preinstalled on most Linux distros; on Windows/macOS either install it or drop any `.ttf`/`.otf` into `assets/fonts/` and set `[subtitles] font` in `configs/slopgen.toml` to its family name.

## YouTube setup

1. Google Cloud Console → create a project → enable **YouTube Data API v3**.
2. OAuth consent screen → add yourself as a test user.
3. Create **OAuth client ID (Desktop)** → download JSON → save as `secrets/client_secret.json`.
4. First `--push` run opens a browser consent window once; the token is cached per account.

**Quota warning:** one upload costs 1600 of the 10 000 daily units → ~6 uploads/day per Google Cloud project. Scale = more projects/accounts (that's what per-account configs are for).

## Honest disclaimers

- YouTube's **inauthentic content** policy (July 2025) demonetizes mass-produced templated content. This tool doesn't exempt you from it: invest in per-channel briefs, voices and assets variety.
- TikTok publishing is a stub (`publish/tiktok.py`) — no official upload API for regular accounts.
- edge-tts is an unofficial use of Microsoft's public endpoint; it can break or be rate-limited at any time.

## Made in Russia 🤍💙🤍

100% vibe-coded via [Claude Code](https://claude.com/claude-code). The author wrote zero lines of code — every function, stage, prompt, and config was generated through conversation with Claude Opus. The ideas, design decisions, and product vision are human; the implementation is AI.

---

<a name="slopgen-ru"></a>

# slopgen (RU)

Фабрика коротких видео промышленного масштаба: **идея → сценарий → нейроозвучка → футаж → сборка ffmpeg с сабами → метадата → публикация**. Полная автоматизация, всё управляется конфигами; TUI для человека, CLI для крона.

## Установка

Нужны **Python 3.12+** и **ffmpeg** в `PATH`. Работает на Linux, macOS и Windows.

ffmpeg: `winget install Gyan.FFmpeg` (Windows) · `brew install ffmpeg` (macOS) · `sudo apt install ffmpeg` / `sudo pacman -S ffmpeg` (Linux).

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -e .
cp .env.example .env             # вписать ключи (Windows: copy .env.example .env)
```

<details>
<summary>Nix / NixOS</summary>

В репозитории есть `shell.nix` (Python 3.12 + ffmpeg + шрифты DejaVu):

```bash
nix-shell                        # при первом входе создаст и активирует .venv
pip install -r requirements.txt && pip install -e .
```

</details>

Личное и копирайтное вынесено в `.gitignore`: `assets/music/`, `assets/footage/`, `assets/ads/`, а также `configs/characters/`, `configs/accounts/` и `configs/ads/*.toml` (кроме `example_vpn.toml`). Занеси свои (правомерные) треки, клипы и персонажей сам — в репозитории лежат только нейтральные шаблоны.

Ключи в `.env`: нейронка — **один** ключ выбранного провайдера (`DEEPSEEK_API_KEY`, `GEMINI_API_KEY` — есть бесплатный тариф, или `OPENROUTER_API_KEY` — есть `:free`-модели); сток-футаж — `PEXELS_API_KEY` / `PIXABAY_API_KEY` (оба бесплатные). Провайдер выбирается в `configs/slopgen.toml` `[llm]` или в TUI → Конфигурация → Нейронка. Больше ключей не нужно: edge-tts без ключа, YouTube — через OAuth. Футаж тоже можно без ключей — через провайдер `local` (`assets/footage/`).

## Быстрый старт

```bash
slopgen                                     # TUI: настроил → START → отошёл

# headless: сначала РЕЖИМ, потом его аргументы
slopgen info ru story                        # ролик «минута инфы»
slopgen info ru cyber --idea "Сайт, знавший даты катастроф"   # своя тема вместо LLM
slopgen info en facts --visuals slideshow --duration 75       # фото-слайдшоу, ~75 секунд
slopgen info ru story --ad example_vpn --push yt_main -n 5
slopgen --preset daily_en                    # пресет — это info-прогон

# ИИ-дорама: озвученная история с постоянным кастом + ИИ-кадры
slopgen drama ru --scenario "Две подруги ссорятся из-за тайны" \
                 --cast example --duration-min 2 --tol 20 --parts 3
slopgen drama en --orchestration my_chain --ad example_vpn --dry-run

slopgen --resume output/<время>_<тип|режим>_<язык>   # продолжить оборвавшийся прогон
```

Одиночный результат: `output/<время>_<тип|режим>_<язык>/<n>/final.mp4` + `metadata.json`.
Многочастные дорамы складываются рядом в той же папке `<n>/` как `part_01.mp4`, `part_02.mp4`, ...

Первый позиционный аргумент — **режим**: `info` (ролик-минутка) или `drama` (ИИ-дорама); он меняет остальную часть команды. Флаги драмы: `--scenario`, `--cast A,B` (имена из `configs/characters/`), `--orchestration`, `--duration-min` (минуты), `--tol` (секунды допуска), `--parts` (количество частей с клиффхэнгерами), `--voice`; плюс общие с `info`: `--ad`, `--ad-mode`, `--profanity`, `--push`, `-n`, `--subs`, `--out`, `--dry-run`. Глобальные (до режима): `--resume`, `--list-types/-ads/-accounts/-presets/-visuals/-characters/-orchestrations`.

**Восстановление после сбоя.** Каждый прогон пишет чекпойнт в `output/<время>_<тип>_<язык>/checkpoint.json` после каждого этапа конвейера. Если прогон оборвался на ошибке (обрыв сети, убитый процесс), пройденная часть (озвучка, скачанный футаж, состояние задачи) сохраняется, а этап и текст ошибки записываются. Команда `slopgen --resume <эта папка>` пропустит выполненные этапы и продолжит с места остановки: готовые видео не трогаются, недоделанные досчитываются. Если прогон завершился с ошибками, в итоговой сводке печатается готовая команда `--resume`.

Приоритет параметров (режим info): **флаги CLI > пресет > дефолты аккаунта > глобальные дефолты**. Аккаунт может нести свои дефолты — `slopgen info --push yt_main` уже валидная команда. Драма собирает параметры прямо из своих флагов (слияния с пресетом/аккаунтом пока нет).

## TUI

`slopgen` без аргументов. Тема **Minecraft**, нижней панели нет — сверху панель с переключателем языка интерфейса RU/EN, кнопкой `<-` (назад) и Palette.

- **Меню** — по центру, выбор стрелочками + Enter.
- **Генерация** — сначала выбор режима (**минута инфы** или **ИИ-дорама**), затем пошаговый визард со списком шагов слева. *Info:* 1) контент (язык, тип, идея, ползунок мата), 2) видеоряд (профиль + переопределения: фон, привязка, интервал, Ken Burns, вставки; длительность), 3) реклама (контракт *или* вручную), 4) публикация (аккаунт, количество, сабы), 5) итог с CLI-командой и кнопкой СГЕНЕРИРОВАТЬ. *Дорама* добавляет шаг **Сюжет** (замысел + переставляемый каст, редактирование справа, фото→внешность через vision, ИИ-заполнение каста), поле количества частей в **Публикации** и превращает шаг «Видеоряд» в **оркестрацию** (упорядоченный список ИИ-генераторов; см. ниже).
- **Конфигурация** — секции слева: профили нейронок (табы профилей, пресеты моделей, ввод API-ключа с автосохранением в `.env`, активация ★), рекламные контракты, аккаунты, пресеты. В секциях сущностей сверху табы — по одному на конфиг-файл плюс `+ новый`; формы предзаполнены, есть 💾 сохранение и 🗑 удаление с подтверждением.
- Выбранная тема оформления сохраняется между запусками (`[ui].theme`).

## ИИ-дорама (`configs/characters/`, `configs/orchestration/`)

Второй режим: **озвученная веб-дорама** — один закадровый рассказчик ведёт историю (и может цитировать реплики героев внутри повествования) поверх ИИ-кадров с постоянным кастом.

- **Каст** (`configs/characters/*.toml`): `name`, `age`, `appearance`. Перед генерацией каждый персонаж один раз компилируется в токен-плотный английский `visual_prompt`, который подставляется в каждый кадр с его участием — чтобы внешность держалась (это текстовый якорь; бесплатные генераторы не фиксируют лицо идеально). В TUI можно собрать каст ad-hoc, подтянуть из библиотеки, загрузить фото (vision → внешность) и дать ИИ заполнить весь каст по замыслу.
- **Оркестрация** (`configs/orchestration/*.toml`): упорядоченный список ИИ-генераторов — `model` (`wan2.1`/`ltx-video`/`animatediff` — видео, `flux`/`turbo` — картинка), `key_mode` (`rotate` — ротация ключей на лимите / `single` — один ключ, потом пропуск), и `metric`+`amount`. Конвейер идёт по этапам, каждый делает свою долю клипов: `percent` — доля бюджета длины, `seconds`/`clips` — абсолютный кусок, последний этап добирает остаток. Несколько API-ключей (по одному на строку в `.env`) ротируются между этапами.
- **Длина, части и синхрон**: задаётся в **минутах** + **допуск** в секундах (история может немного выйти за рамки). Если частей больше одной, сценарист размечает сцены по частям и ставит обрывы на клиффхэнгерах. Одна сцена = один клип; озвучка синтезируется посценно и растягивается (ffmpeg `atempo`) под длину клипа, тайминги субтитров пересчитываются — звук и видео синхронны. Нативная реклама вплетается в сюжет на уровне сценария, а не вклеивается отдельно.

Запуск из TUI (Генерация → ИИ-дорама) или headless: `slopgen drama ru --scenario "…" --cast example --duration-min 2 --tol 20 --parts 3 --orchestration my_chain`.

## Профили видеоряда (`configs/visuals/`)

Видеоряд — слоёная композиция, настраивается профилями:

- **Фон**: `stock_video` / `stock_photo` / `local_video` / `local_photo` / `ai_photo` / `ai_video` (бесплатная генерация без ключей — картинки Pollinations, видео Wan через HF Spaces). Привязка `narration` — нейронка выдаёт запрос картинки/футажа на каждые ~N секунд речи, привязанный к тому, что произносится в этот момент (Швейцария → столица, пара → пара, щенок → щенок); `neutral` — случайный/зацикленный контент (например геймплей). Фото-фон получает движение Ken Burns (`none`/`subtle`/`strong`) и меняется каждые `interval_s` секунд.
- **Передний план**: опциональные вставки-картинки/клипы в рамке — *по событию, а не по таймеру*: нейронка сама решает, какие произносимые фразы заслуживают иллюстрации, и каждая вставка показывается ровно пока звучит её фраза (тайминг из пословной разметки edge-tts) и исчезает после. Ты задаёшь только источник, ширину и позицию.

Готовые профили: `classic` (сток-видео, дефолт), `slideshow` (фото в такт тексту с Ken Burns), `gameplay` (кинь клипы майнкрафт-паркура/сабвей-сёрфа в `assets/footage/gameplay/` — поверх будут выскакивать картинки по тексту). В TUI шаг «Видеоряд» предзаполняется профилем; любое изменённое поле превращает запуск в кастомный профиль.

## Профили нейронок (`configs/llm/`)

Именованные подключения: `provider` (`deepseek`/`gemini`/`openrouter`/`custom`), `model`, `base_url`, `temperature`, `web_search`. Активный выбирается через `[llm].profile` в `slopgen.toml`. Ключи API никогда не лежат в TOML — только в `.env`; в TUI (Конфигурация → Профили нейронок) есть пресеты моделей по провайдеру, ввод ключа (сам сохранится в `.env`), тумблер веб-поиска, активация и удаление профилей.

**Веб-поиск** (`web_search = true`): даёт модели настоящий инструмент `web_search` через стандартный function calling. Перед написанием сценария модель сама его вызывает, слопген выполняет бесключевой поиск DuckDuckGo и возвращает результаты — так озвучка опирается на реальные проверенные факты, а не на выдуманные имена/события. Работает на любом провайдере, чья модель поддерживает tool-use (OpenAI, DeepSeek, OpenRouter, compat-эндпоинт Gemini); модель без tool-calling просто не станет его использовать.

Ключи стоков (Pexels, Pixabay) тоже можно вставить в TUI: **Конфигурация → Ключи API футажа** — они сохраняются в `.env`. Нужны только для `stock_*` видеоряда; локальным ассетам не требуются.

## Конфиги (`configs/`)

Всё — редактируемый руками TOML; новый файл в папке = новая сущность без кода:

- `slopgen.toml` — глобальный (видео, целевая длительность, сабы, музыка, активный LLM-профиль, порядок провайдеров футажа, язык/тема интерфейса);
- `content/*.toml` — типы контента: брифы промптов по языкам, голоса edge-tts, fallback-ключевые слова;
- `ads/*.toml` — рекламные контракты: ссылка, секция overlay (ассеты, подпись, позиция, тайминг), секция native (ассеты, talking points для вплетения в озвучку), сниппет для описания;
- `accounts/*.toml` — площадки публикации + их дефолты;
- `presets/*.toml` — бандлы параметров для запуска одной командой.
- `characters/*.toml` — каст ИИ-дорамы (`name`, `age`, `appearance`, компилируемый `visual_prompt`).
- `orchestration/*.toml` — цепочки ИИ-генераторов для дорамы (упорядоченные `[[stages]]` с `model`/`key_mode`/`metric`/`amount`).

## Ассеты (`assets/`)

`ads/<контракт>/overlay/` — угловые анимации (.webm с альфой, .gif, .png); `ads/<контракт>/native/` — готовые рекламные вставки; `music/` — фоновые треки (берётся случайный, тихо подмешивается); `fonts/` — шрифты сабов; `footage/` — локальные клипы для провайдера `local`; `footage/gameplay/` — фоновые лупы для профиля `gameplay`; `images/` — локальные картинки для фото-фона и вставок. Текущие демо-файлы — заглушки для теста, замени их настоящими.

## Настройка YouTube

1. Google Cloud Console → проект → включить **YouTube Data API v3**.
2. OAuth consent screen → добавить себя в test users.
3. Создать **OAuth client ID (Desktop)** → скачать JSON → положить в `secrets/client_secret.json`.
4. Первый запуск с `--push` один раз откроет браузер для согласия; токен закэшируется.

**Квота:** загрузка = 1600 из 10 000 юнитов/день → ~6 загрузок/день на один Google Cloud проект. Масштаб — через несколько проектов/аккаунтов (для этого и есть конфиги аккаунтов).

## Честные дисклеймеры

- Политика YouTube **inauthentic content** (июль 2025) демонетизирует шаблонный масс-контент. Инструмент от неё не спасает: вкладывайся в разнообразие брифов, голосов и ассетов по каналам.
- Публикация в TikTok — заглушка (официального API нет).
- edge-tts — неофициальное использование публичного эндпоинта Microsoft; может отвалиться в любой момент.

## Сделано в России 🤍💙🤍

100% навайбкожено через [Claude Code](https://claude.com/claude-code). Автор не написал ни одной строчки кода — каждая функция, стадия, промпт и конфиг сгенерированы в диалоге с Claude Opus. Идеи, дизайн-решения и продуктовое видение — человеческие; реализация — ИИ.
