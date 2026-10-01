# slopgen

Industrial-scale short-form video factory: **idea → script → TTS voiceover → stock/AI footage → ffmpeg assembly with subtitles → metadata → publish**. Fully automated, config-driven, with a TUI for humans and a CLI for cron.

*Русская версия — [ниже](#slopgen-ru).*

---

## Requirements

- **Python 3.12+**
- **ffmpeg** on your `PATH` (the assembly engine)
- Internet access (edge-tts, stock/AI APIs, your LLM provider, YouTube)
- Optional: `pip install -e '.[azure]'` for the Azure voice engine. Neural weights are **not** dependencies — `slopgen models install …` fetches them on demand.

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

# Fandom: a video set inside a world you wrote down, told from inside it as fact
slopgen fandom ru example --scenario "Что делают с сумками, за которыми никто не пришёл"
slopgen fandom ru example --narrator chronicler --duration-min 3 --parts 2

# generate without publishing (demo assets included)
slopgen info en cyber --ad example_vpn --dry-run

# on a loop: one video after another, until you stop it
slopgen info ru facts --loop                          # topics from the model, no limit
slopgen info ru --loop --loop-limit 20 --topics me    # twenty, each topic yours to give
slopgen info ru facts --loop --loop-ahead 5           # ...and five topics kept ready to read
slopgen loop topic "why bread goes stale"             # steer it from another terminal
slopgen loop queue  /  loop for 1,3 duration=90       # the queue, and one video's own settings
slopgen loop source ai   /   loop breaks script   /   loop limit 5   /   loop stop

# from a browser, or from a phone: the same panel, the same runs
slopgen web                                           # http://127.0.0.1:8770
slopgen bot --detach                                  # Telegram: chat + Mini App
```

Single-part output lands in `output/<timestamp>_<type|mode>_<lang>/<n>/final.mp4`, plus
`metadata.json` when the run was asked for one (`--metadata`; see the flag below).
Multi-part dramas produce `part_01.mp4`, `part_02.mp4`, ... together in that same `<n>/` directory.

### CLI reference

The first positional argument is the **mode**: `info` (the minute-of-info clip),
`drama` (the AI web drama) or `fandom` (the same narrated shape, set inside a world
you wrote down). Each mode shapes the rest of the line. Running `slopgen` with no
mode opens the TUI.

**`info LANG TYPE [flags]`**

| Argument / flag  | Meaning                                                                                                                 |
| ---------------- | ----------------------------------------------------------------------------------------------------------------------- |
| `LANG` `TYPE`    | positional: language (`en`/`ru`) and content type (`story`/`cyber`/`psych`/`facts`)                                     |
| `--idea "..."`   | your own topic; omit to let the LLM invent one                                                                          |
| `--visuals NAME` | visuals profile from `configs/visuals/` (default `classic`)                                                             |
| `--duration N`   | target spoken length in seconds (default 45; >60 is fine, Shorts allow up to 3 min). A hint for the LLM, not a hard cap. **`0` = the model chooses it** |
| `--profanity N`  | swearing level in the narration, 0 (clean) … 100 (constant); in the TUI it's a slider on the Content step               |
| `--ad NAME`      | ad contract from `configs/ads/`                                                                                         |
| `--ad-mode`      | `overlay` (corner animation + caption), `native` (spoken mention + ad clip), `both`                                     |
| `--push NAME`    | account from `configs/accounts/`; omit → save locally                                                                   |
| `-n, --count N`  | videos per run                                                                                                          |
| `--preset NAME`  | load a parameter bundle from `configs/presets/`                                                                         |
| `--resume DIR`   | continue a crashed run from its output dir (the folder holding `checkpoint.json`)                                       |
| `-b, --break STAGE` | stop for review after this stage (repeatable): `idea` `script` `tts` `footage` `subtitles` `assemble` `metadata`      |
| `--subs`         | subtitle style: `word_pop` / `phrases` / `karaoke`                                                                      |
| `--tts-rate N`   | speech rate offset in percent (-50 … +50); a slider on the Content step, and a single fragment can be re-voiced at another speed at the `tts` breakpoint |
| `--voice NAME`   | narrator voice: a catalogue id for the active engine (`ru-RU-SvetlanaNeural`), a `configs/voices/` clone (`марта` — whatever delivery that card points at), or one of its deliveries by name (`марта:зло`). Default comes from the content type |
| `--deliveries`   | let the writer cast the intonations: the shouted and whispered lines are pinned to the take that is. Cloned voice with several deliveries only — one LLM call, before anything is voiced |
| `--tts-engine E` | `edge` (default) · `azure` · `qwen` · `qwen-local` — see [Voice engines](#voice-engines-and-cloning-tts-configsvoices-slopgen-models) |
| `--tts-source S` | `engine` (default) or `manual` — write the script out and wait for your own recordings |
| `--clean-subs`   | swap profanity out of the burned-in subtitles; the voiceover keeps every word                                           |
| `--visual-style "…"` | how the picture should LOOK, in your own words ("anime"; "grainy 16mm, sodium street light"). Compiled into tags glued onto every generated prompt — see [Art style](#art-style-one-description-every-prompt) |
| `-F, --filter NAME[=DOSE]` | lay a montage filter over the whole finished video (repeatable): `bw` `film` `bloom` `vignette` `grain` `vhs` `crt` `glitch`, dose 0-100 (bare name = 60) — see [Filters](#filters-one-look-over-the-whole-cut) |
| `--out DIR`      | output dir override                                                                                                     |
| `--dry-run`      | generate but don't publish (dev tool; picking "save locally" does the same)                                             |
| `--metadata`     | write a title, a description and tags for the finished video. **Off by default** — it is the one stage whose output nothing downstream reads, so a video you are only going to watch here doesn't pay a model for it. A run that actually publishes gets metadata regardless (a video cannot be uploaded without a title), and so does a run with a `metadata` breakpoint |
| `--music NAME`   | what plays under the voice: a track by its path under `assets/music/` (`эпичное/opening.m4a`), or a FOLDER with a trailing slash (`эпичное/`) to draw from that shelf only; `none` for silence. Default: drawn from everything (seeded on the run, so the montage room plays the very track the render will use) |
| `--keep-temp`    | keep intermediate ffmpeg files                                                                                          |

**`drama LANG [flags]`** — shares `--ad`, `--ad-mode`, `--profanity`, `--push`, `-n/--count`, `--subs`, `--clean-subs`, `--tts-rate`, `--deliveries`, `-b/--break`, `--out`, `--dry-run`, `--metadata`, `--music`, `--keep-temp` with `info`, plus:

| Flag                   | Meaning                                                                        |
| ---------------------- | ------------------------------------------------------------------------------ |
| `LANG`                 | positional: narration language (`en`/`ru`)                                     |
| `--scenario "..."`     | the plot/premise; omit to let the LLM invent one                               |
| `--cast A,B`           | comma-separated character names from `configs/characters/`                     |
| `--orchestration NAME` | AI-generator chain from `configs/orchestration/` (default: one `wan2.1` stage) |
| `--duration-min N`     | target length in **minutes**. **`0` = the model reads the premise and decides** |
| `--clip-s N`           | AVERAGE length of one generated clip in seconds (0 = each generator's own); the writer sizes each beat around it |
| `--visual-notes "…"`   | constraints on what the shots may SHOW, never on the story ("all weapons are toy ones") |
| `--visual-style "…"`   | how the picture should LOOK, in your own words; compiled into tags glued onto every generated prompt |
| `-F, --filter NAME[=DOSE]` | lay a montage filter over the whole finished video (repeatable), on every episode alike — see [Filters](#filters-one-look-over-the-whole-cut) |
| `--clean-subs`         | swap profanity out of the burned-in subtitles; the voiceover keeps every word |
| `--tol N`              | how many **seconds** the finished video may run over/under the target          |
| `--parts N`            | split one drama into N publishable parts; script cuts are planned as cliffhangers. Only what is *asked* of the writer — the boundaries are then yours to move at the `script` and `cut` breakpoints |
| `--parts-at-once`      | cut every part together at the end, instead of finishing each as soon as its own clips are in |
| `--voice NAME`         | narrator voice: a catalogue id for the active engine, a `configs/voices/` clone (`марта` — whatever delivery that card points at), or one of its deliveries by name (`марта:зло`) (default per language) |
| `--tts-engine E`       | `edge` (default) · `azure` · `qwen` · `qwen-local`                              |
| `--tts-source S`       | `engine` (default) or `manual` — you supply the recordings                     |
| `--deliveries`         | let the writer cast the intonations: the shouted and whispered lines are pinned to the take that is (cloned voice with several deliveries only) |
| `--tts-rate N`         | speech rate offset in percent (-50 … +50). The writer counts on it: a faster voice fits more story into a clip of the same length, so each beat is written longer |

**`fandom LANG FANDOM [flags]`** — the drama's command line with a world put in front of it. It takes the drama's `--orchestration`, `--voice`, `--tts-engine`, `--tts-source`, `--deliveries`, `--visual-notes`, `--visual-style`, `-F/--filter`, `--tts-rate` and the shared `--ad`, `--ad-mode`, `--profanity`, `--push`, `-n/--count`, `--subs`, `--clean-subs`, `--out`, `--dry-run`, `--metadata`, `--music`, `--keep-temp`. It does **not** take `--parts`/`--parts-at-once` (it is never cut into episodes), nor the drama's timing flags: a drama is authored in minutes with a tolerance and an average clip length, because its clips are bought from a generator whose free tier you are rationing. This one is cut to a length, and the writer sizes every shot itself. Its own flags:

| Flag                | Meaning                                                                                       |
| ------------------- | ----------------------------------------------------------------------------------------------- |
| `LANG` `FANDOM`     | positional: narration language (`en`/`ru`), then the folder name under `configs/fandoms/`     |
| `--scenario "..."`  | the brief: **what about this world to tell**, or which theory about its records to argue. Omit and the writer picks something out of the world itself |
| `--narrator`        | who is telling it: `resident` (lives there, first person, the world as daily life) or `chronicler` (studies its records, builds theories, no "I" protagonist). Default `resident` |
| `--invent`          | how far the writer may add to the world where its records stop: `no` (the default — the records are the whole world and a gap is simply not known), `gaps` (only where a beat cannot be written otherwise, and only the smallest ordinary detail that unblocks it) or `free` (furnish the world at will). All three forbid contradicting a record, inventing the piece's own subject, coining a name for what the records leave unnamed, and settling what they leave open |
| `--shapes NAME`     | which catalogue of **piece shapes** to plan out of, by name under `configs/shapes/`. Default: the world's own `shapes`, else the shipped `default` |
| `--shape NAME`      | force the shape of this one piece — a name from that catalogue (`mechanism`, `duties`, `rule`, `vignette`, `choice`, `procedure`, `creature`, `incident` in the shipped one). Omit and the plan picks one out of the brief and the records; naming one is how a run of alike videos is made, and how you overrule a planner that keeps reaching for the same form |
| `--duration SEC`    | how long the finished video runs, in **seconds**. No tolerance and no clip-length flag: the writer gives every beat its own length (2-12s) and sizes the narration to fit. Default 120; **`0` = the model reads the brief and decides** |
| `--medium`          | what the picture is made of: `video` (clips) or `photo` (a slideshow of stills, held and slowly panned). It also decides what may make them, so it constrains `--source` |
| `--source`          | what makes the shots: a generator (`wan2.1`, `ltx-video`, `animatediff` for video; `flux`, `turbo` for photo), `manual` (you generate them) or `search` (you find them). Default `wan2.1` for video, `flux` for photo |
| *(no `--cast`)*     | there is nothing to pass: the world's characters live in `configs/fandoms/<name>/characters/` and come with it. Adding one for a single video is exactly what a world does not do — a character is in it or is not. Nor need it be a person, nor even one of anything: a creature, a machine, a vessel, a body of identical faceless figures or a whole race is described the same way — as a look, with personalities left to the lore |
| `-b, --break STAGE` | `canon` `script` `entities` `tts` `footage` `subtitles` `assemble` `metadata` — the drama's, plus `canon` of its own and minus `cut` (there are no episodes to re-cut) |

**Global** (before the mode, or standalone): `--resume DIR`, and the inspectors
`--list-types` `--list-ads` `--list-accounts` `--list-presets` `--list-visuals`
`--list-characters` `--list-orchestrations`.

**Subcommands** that reopen a run which parked itself, each straight into the screen
it is waiting on: `slopgen gather [DIR]` (footage you supply — hand-made clips or found ones) and `slopgen review [DIR]`
(a breakpoint). Omit the directory and the latest such run is found.

`slopgen usage DIR` prints what a finished or parked run spent on the LLM — tokens per
stage, how many calls were retries, how much of the input the provider served out of its
prompt cache, and the cost where the profile carries prices (`--calls` lists every
request individually). It reads the run's own checkpoint, so it answers per video what a
provider's dashboard only answers per day.

Parameter priority (info mode): **CLI flags > preset > account defaults > global defaults**. An account config can carry its own default language/type/ad, so `slopgen info --push yt_main` alone is a valid command. Drama builds its parameters directly from its own flags (no preset/account merge yet).

**Crash recovery.** Every run is checkpointed to `<out>/<stamp>_<type>_<lang>/checkpoint.json` after each pipeline stage. If a run dies partway (network drop, killed process), the finished stages' outputs (TTS audio, downloaded footage, the job state) are kept, and the failing stage + error are recorded. Re-run with `slopgen --resume <that dir>` to skip the completed stages and continue from the point of failure — already-finished videos are left untouched, unfinished ones pick up where they stopped. When a run ends with failures, the summary prints the exact `--resume` command to use.

**Breakpoints.** Tick any pipeline stage on the wizard's **Summary** step (or pass `--break STAGE`, repeatable) and the run parks right after that stage instead of walking on — the checkpoint holds it in a `review` state, and a review screen shows what the stage produced as a list of editable lines:

| Breakpoint  | What you get                                                                                            |
| ----------- | ------------------------------------------------------------------------------------------------------- |
| `idea`      | the chosen topic, before a single line is written                                                       |
| `canon`     | *(fandom)* the world's compiled canon sheet, as one editable block — fix a wrong date, add the custom the compiler skipped, cut what this video must not lean on. The last free moment to fix the world: every window of the script is written against this sheet, and what is not in it does not exist in the world. The edit belongs to this run, not to the folder — `configs/fandoms/` is left as it was |
| `script`    | the script as written — per scene: the spoken line, its shot prompt (or search terms), the cast in frame, the generator and the clip length. The only place to fix a shot before it is generated |
| `entities`  | *(drama)* the visual registry: everything that recurs across shots and is not cast — a machine, a location, a prop, a nameless regular, an unusual crowd. Per entry: the name the shot prompts use for it, a note, and the English descriptor the generator gets. Editing one descriptor restyles every shot showing it |
| `tts`       | every voiced fragment with the length that came out; edit a line and **🔊 re-voice** it right there (▶ to listen), as many takes as you like — only that line is re-synthesized. A **speed slider** sits above the buttons: one for the whole screen, applying to whichever fragment you re-voice with it. That line then keeps the speed (and the card says so) while the rest of the video stays at the run's |
| `cut`       | *(drama)* where each episode ends. The scenes are read-only here, each showing the seconds it really runs to; what you move are the **part markers** — the last free moment to re-cut, since the next stage generates (or asks you to hand-make) the clips against these boundaries |
| `picture`   | *(fandom, frame base)* the picture track — and it does not open as a list, it opens as an **editing room**: see [the montage room](#the-montage-room) |
| `footage`   | the shot prompt (drama) or search queries (info) per scene; changed scenes get their footage remade      |
| `subtitles` | the generated `.ass` files as text, written straight back to disk                                       |
| `assemble`  | the rendered file(s) — inspect-only, watch them before publishing                                       |
| `metadata`  | title, description and tags, right before publish. The stage is off unless asked for (`--metadata`), and ticking this breakpoint is one of the ways of asking — there is no sense in parking to read text nobody wrote |

The screen is master-detail in the terminal: the stage's items are **cards** on the left — **＋** adds one, **▲ ▼** reorder, **✖** drops — and the open card's fields are edited on the right. The browser draws the same document as one column of cards, all fields open, with the same **＋ ▲ ▼ ✖** on each and drag-and-drop by the handle; everything below is true of both. (It was not, for a while: the browser used to flatten the document into identical unlabelled boxes with nothing addable, droppable or movable, and a part marker showing as the raw string `bp.f.part` — the pipeline had supported all of it from the start, and only the browser had not.) A drama scene carries its spoken line, its shot prompt, who is in it (added and removed one by one from the run's cast), which generator makes it and how long the clip runs. Fields are editable by hand (except the `.ass` files, where a wholesale model rewrite would mangle the cue timings), and an **AI edit line** sits under the list. At the `script` breakpoint it works on the scene list as a whole and may do anything the instruction asks — rewrite any field, reorder, merge, split, add or drop scenes, recast them, switch generators — carrying each scene's identity along so an untouched one keeps the audio and clip already made for it. Elsewhere it rewrites the free-text lines: describe the change ("shorter", "make scene 3 angrier", "split this beat in two") and the model rewrites the whole set — for script/voiceover it may also change how many fragments there are. A drama also shows **part markers** among the cards — `── part 2 starts here ──` — and everything below one belongs to that episode. On `script` and `cut` they are yours to move (**▲ ▼**), to add (**＋ Part break**, splitting the drama further) and to drop (merging an episode into the one above); everywhere else they are drawn read-only, so you can always see which episode you are looking at. This is what sets the number of parts — `--parts` only asks the writer for a starting point. Press **Continue** and the run picks up from there; a breakpoint fires once per video, so a re-run of the stage you just edited won't park again. A parked run can be deleted like any other, and the second press says so plainly (*it is waiting to be reviewed — delete it and its folder?*) rather than asking a generic "sure?": one went to `rmtree` from the very row that was asking to be reviewed, and the fault there was the ease of it, not the existence of the button — a parked run is exactly the one you usually want rid of. Items are numbered by **where they sit**: drop the second of five and the three below it renumber, which they did not before — the caption was the number the item had when the document was built, so after one deletion two cards could both call themselves #3 (an ad break's `#3 · AD` keeps its marker; only the number moves). **Every** item may be dropped, the last one included: refusing that was meant to keep a document with something to say, and it stood between you and the commonest reason to open this screen — clearing the board and writing the piece yourself. The shape of the last item removed is kept, so **＋** still builds a fresh one on an emptied document; what a document may not be is empty when it is *applied*, and that is where it is now checked and said out loud instead of the pipeline silently keeping the old scenes. The AI edit line is also told **where the video is set**: on a fandom run it gets the world posture and the canon sheet the writing stages get. Without them it was the one thing in the run that had never heard of the world, reading lines written from inside it as ordinary sentences — «расскажи про мой первый день на объекте» came back as a construction site with an alarm clock and a hard hat, which is what those words mean to anyone who has not read the records. With `-n` >1 the videos queue up and are reviewed one after another. All three modes support it. Headless runs print `slopgen review <dir>` to reopen the parked run (same as `slopgen gather` for manual clips). With `--tts-source manual` the voiceover parks the same way, and the recordings go into `manual_voice/inbox/`.

**Generation in a loop (`--loop`).** A batch (`-n 5`) settles everything before it starts:
five videos, one set of settings, one source of topics, and nothing to be said to it once
the first one begins. A loop is the same work one video at a time, with the decisions kept
open — three of them are read again before every video, and all three can be changed while
it runs. Add `--loop` to any mode (`--loop-limit N` caps it, `0`/omitted = no limit;
`--topics ai|me` says who picks each topic; `--on-park hold|go_on` says what a video that
stops for review means; `--loop-ahead N` keeps N invented topics waiting where you can read
them). The topic you started it with is used for the first video whoever picks the rest.

**A topic in the queue is always used; the source only decides what happens when the queue
runs out** — under `ai` the model invents one and the loop never waits, under `me` it waits
for you. Standing in a terminal it ASKS: type a topic for the next video, press Enter to let
the model take this one, or type a command — `!ai` `!me` `!limit N` `!breaks script,tts`
`!park hold|go_on` `!ahead N` `!queue` `!stop`. From anywhere else, the same edits are
subcommands, and each lands on the next video rather than in the middle of the one being
made:

| Command | What it does |
| ------- | ------------ |
| `slopgen loop status [DIR]` | what it is doing, what it has made, what is queued |
| `slopgen loop list`         | every loop under the output folder, newest first |
| `slopgen loop queue [DIR]`  | the videos not made yet, numbered, with whatever each of them asks for |
| `slopgen loop topic "..." [...]` | queue topics for the next videos (`--at N` puts them somewhere else than the end; `--me` also hands the rest over to you) |
| `slopgen loop edit N "..."` | retype one queued video's topic, its own settings untouched |
| `slopgen loop move N TO`    | put a queued video somewhere else in the queue |
| `slopgen loop drop WHICH`   | take videos off the queue (`2` · `1,3` · `2-5` · `all`) |
| `slopgen loop for WHICH KEY=VALUE ...` | give queued videos their **own** answer to a setting; `--clear KEY` hands it back to the loop |
| `slopgen loop ahead N`      | how many topics the model keeps ready in the queue (`0` = one at a time, unseen) |
| `slopgen loop source ai\|me` | who picks the topic once the queue runs out |
| `slopgen loop limit N`      | cap it, or uncap it (`0`) |
| `slopgen loop breaks [STAGES]` | which stages stop for review from the next video on; no argument = none |
| `slopgen loop park hold\|go_on` | whether a parked video holds the loop |
| `slopgen loop set KEY=VALUE ...` | **any generation setting**, from the next video on (see below) |
| `slopgen loop show`         | every setting the loop is on, and the name to type to change it |
| `slopgen loop stop`         | end it after the video being made now; that video is never torn in half |
| `slopgen loop go [DIR]`     | pick a stopped loop back up in this terminal, on the plan it already has |

Omit `DIR` and the loop most recently written to is the one meant. The plan lives in
`output/loop_<stamp>_<type>_<lang>/loop.json` and is a plain file: anything that can edit it
can steer the loop, including the browser, where a loop is a card at the top of the runs tab
with the same controls on it.

**The queue is the plan, and an entry in it is a whole video.** Not a line of text: it
carries the topic *and* its own answers to the settings wherever it wants to differ from
the loop — a longer length, one breakpoint just this once, no ad on this one. They are
folded over the loop's settings when that entry comes up, and everything the entry does not
answer stays the loop's. So a queue of ten is ten videos already decided rather than ten
reminders to come back and retune between them, which is the point: fill it, start it, go
to bed.

```bash
slopgen loop queue                                  # 1..N, with each one's own settings
slopgen loop for 1,3 duration=90 breaks=script      # two of them, longer and reviewed
slopgen loop for all fx=crt=40,grain=20             # all of them, nothing else touched
slopgen loop for 2 --clear duration                 # #2 goes back to the loop's length
slopgen loop move 4 1   /   loop drop 2-5           # reorder, or throw away
```

`for` takes one setting and any number of videos on purpose. An edit that carried a whole
form would carry every setting you did not mean to change with it, and six videos would
quietly come out identical. Names and values are the ones `loop set` takes, short names
included, and a value the settings cannot hold is refused as you type it.

**Topics thought of before they are needed (`--loop-ahead N`, `slopgen loop ahead N`).** A
loop picking its own topics normally invents each one at the moment it is used, which means
nobody ever sees it — you find out what the video was about by watching the video. Set
`ahead` and the model instead keeps that many topics **waiting in the queue**, as ordinary
entries you can read, rewrite, reorder, give their own settings, or throw away before they
become anything. They are marked as the model's, and it is told what is already queued and
what this loop has already made so it does not hand back the same topic twice. It is
best-effort by design: a model that will not answer costs the lookahead and nothing else,
and the loop goes on inventing that one at the writing stage as it always did.

What it puts there is a **topic** — one line, ten words, *how do you get onto the Object?*
— and not a brief. A fandom loop used to queue paragraphs, and that was worse than untidy:
a video's writer reads a short brief as a topic to build a piece around and a long one as
the piece itself, so a queue of paragraphs was a queue of videos nobody had agreed to, each
already deciding what it was before anyone read it. The cause was a slot doing two jobs.
`write_brief` lets the operator's own instruction decide the shape and the length of what it
writes, with no cap — which is right in the wizard, where the instruction is the operator's
— and a loop passes an instruction too: the machine-written *invent a fresh subject, not any
of these*. Merely being non-empty, it switched the prompt into "no cap" and the queue filled
with finished scripts. A caller asking for a topic now says so in the argument meant for it,
and that outranks an instruction it wrote itself.

**Every setting, on the fly.** Not only who picks the topics: everything the run was
started with is a setting of the loop, and any of it can be rewritten between two videos
— the length, the look, the montage filters, the voice and its speed, the subtitles, the
cast, the world, the generator chain, the ad contract, where it publishes. Type them as
`key=value`, by short name or by field name:

```bash
slopgen loop set duration=90 style="16mm, sodium street light" fx=crt=40,grain=20
slopgen loop set voice=ru-RU-SvetlanaNeural rate=-10 subs=karaoke clean_subs=yes
slopgen loop set cast=Алекс,Кирилл parts=3 tol=20        # a drama, re-cast mid-loop
slopgen loop set world=Хлябь narrator=chronicler source=wan2.1   # a fandom, re-aimed
slopgen loop set ad=example_vpn push=yt_main dry_run=no  # start publishing for real
slopgen loop show                                        # what it is on right now
```

`slopgen loop show` lists the lot with the short name beside each. A value naming a
config that does not exist — an account, an ad contract, a world, a filter, a character
— is refused **as it is typed**, because a loop is unattended by design and the
alternative is finding out from a video that failed at its last stage while you slept.

Five things are not settings and each has somewhere else it lives: the **mode** (that is
another loop), the **count** (the loop is the thing that repeats), the **output folder**,
and the **topic** — that is the queue's, above. Everything else is fair game. In the
browser it is the same set and literally the same form: the loop's card has a
**settings** button that opens the mode's start form filled in from the loop, with its
launch button reading *apply to the loop*.

**In the browser the queue is edited where it is read.** The loop's card on the runs tab
holds the whole list: drag a row by its handle (or **↑ ↓**) to reorder, type in it to
retype the topic, **×** to drop it, **✨** to ask the model for three more. **⚙** opens
that one video's settings *inside the row* — length, breakpoints, rehearsal at once, and
everything else one press below under *more settings*; nothing here opens another tab,
because walking to another screen to change one video's length is how a queue stops being
worth keeping. A field the video does not answer shows the loop's own value, greyed:
touch it and it becomes this video's, press **↺** and it is the loop's again. The **⚙** on
a closed row carries a count, so a row says whether it differs from the loop without being
opened.

**Changing one setting across many videos** is the same panel with the videos ticked. Tick
the rows, and a form appears above them with a tick beside every field: only the ticked
fields are written, everything else about those videos is left exactly as it was. Touching
a control ticks its field, and a ticked field jumps to the top of the panel and lights up,
so what is about to be written is one short list rather than something to find again among
thirty controls. The same bar duplicates and deletes the picked videos.

**A change lands on the next video, never in the middle of one.** The video being made
was launched on the settings that stood when it started, which is the only way a setting
can mean one thing for a whole video. The plan file is written by several hands at once —
the loop recording a video, the browser retuning it, a terminal queueing a topic — so
every write takes a lock, re-reads what is there and puts back only its own half.

**The videos are ordinary runs.** They land in the output folder beside every other one, so
`slopgen review`, `slopgen gather`, the TUI's run list and the browser's reach them without
knowing a loop exists. By default a video that parks — on a breakpoint, or waiting for
hand-made clips — **holds** the loop until you have dealt with it, because starting the next
one is exactly what stops you finishing the one that asked you a question. `--on-park go_on`
says carry on regardless. A loop also stops itself after three failures in a row: an
unlimited loop and a dead API key would otherwise spend the night failing in a tight ring.

**A loop survives a restart of the server.** The plan is a file (`output/loop_*/loop.json`),
so it outlives the process exactly as a checkpoint does — but for a while only the *runs*
were read back off disk at startup and the loops were not, which meant restarting the web
server emptied the Loops tab while every plan sat there untouched: the queue, its per-video
settings and the whole steering panel had no card left to live on. Both are adopted now. A
loop comes back **stopped** and is never resumed on its own — a server is most often
restarted to make something stop, and a restart that quietly picked six queued videos back
up in the background is the opposite of what was asked — and a plan whose file still says
`running` is stamped stopped as well, since the thread that wrote that word died with the
process before it. Everything on the card works on an adopted loop: the queue, ⚙ on each
entry, and **loop settings**, which is where a world picked wrong gets picked again.
An ended loop also carries **carry on** and **delete the loop**, neither of which
existed while every loop on the page was one the wizard had made moments earlier —
"stopped" and "gone" looked the same, so the card only ever needed a stop button.
Carrying on resumes the plan as it stands: the remaining topics, the same settings,
the same tally, never the finished videos over again (`LoopFile.restart` clears the
stop flag and the run of failures that ended it, because those live in the file and a
thread started without clearing them would read them and end again at once). Deleting
takes the plan folder and **leaves every video the loop made** — those are ordinary
runs in folders of their own, and half the reason to throw a plan away is that the
queue was wrong while the output was fine. It asks twice, like the runs list does.

**Stop, and stopping.** `stop` ends the loop after the video being made now — that
video is never torn in half, because one torn in half has spent its quota and produced
nothing. So a stopped loop keeps working for as long as that video takes, and the card
now says so (*stopping — finishing the video it began*) instead of going on saying
*running* under a button that has already been pressed. That silence was the whole
problem: the flag was written to the plan, nothing showed it, and the only reading left
was that the button did not work. Beside the words is the one thing that does end it
sooner — **drop that video too**, which stops the run in flight as well, asks twice
because it throws away work already paid for, and is a separate button precisely so
that `stop` can go on meaning what it has always meant. And the launch button is now
one press however many the mouse sends: a double click used to start two runs, or two
loops with the same queue, each with a thread of its own.

**…and what "after the current stage" used to be able to mean.** A run is asked to stop
by a flag, and the flag was read BETWEEN stages. For a pipeline of short steps that is
the right place; for the long ones it was not a place at all. A footage stage fetching
seven generated backgrounds is seven round trips into somebody else's queue, and
`gradio_client` has no timeout, nor do the sockets under it — so a Space that took the
job and never answered held the stage, and the stage held the run, and the page went on
saying *will stop after the current stage* about a stage with no intention of ending.
Measured on the run that produced this: three hours at one shot of seven, the peer's end
of the connection already closed, and no way to end it but restarting the server. Two
things changed. The long stage now asks `ctx.stopping()` once per scene and raises
`Stopped`, which parks the run exactly as the between-stages answer does — raising and
not returning, because a stage that returns is recorded as finished and a footage stage
that filled two scenes of seven would send the run on to be assembled out of nothing.
And one call into a Space is now bounded (`SLOPGEN_SPACE_TIMEOUT_S`, 600s by default, `0`
for the old unbounded wait): generous, because a queued Space legitimately takes minutes
and a timeout that fires on a working one costs a clip for nothing.

**Length on the model's word (`0`).** Put `0` where a length goes — `--duration`, `--duration-min`, or the Length field in the TUI — and nobody buys one: the model chooses it from the material. What it is worth is decided by the brief, so a brief that is already a finished text runs as long as saying it takes, a premise with three turns in it gets the time those turns need, and a bare topic gets what the format wants. Every mode takes it, and they arrive at it differently for a reason. An info clip needs no extra request at all: its script is one call and the video is exactly as long as the narration came out, so the writer is simply told to choose. A drama or a fandom video has to know first — the length is what the shot list is cut from, and the number of shots decides how many passes the script is written in — so the run makes one small call that reads the brief and answers with seconds, prints what it chose and why, and then proceeds exactly as if you had typed that number, budget checks and all. Being held to a length is not weaker for the model having picked it.

## TUI

`slopgen` with no arguments. Custom **Minecraft theme**, no footer — the top bar holds the RU/EN interface-language toggle, the `<-` back button and the command Palette.

- **Home** — centered menu, arrow keys + Enter.
- **Generate** — first pick a mode (**minute-of-info**, **AI drama** or **Fandom**), then a step-by-step wizard with a vertical step list on the left. *Info:* 1) content (language, narrator voice, type, your own idea, profanity and speech-rate sliders), 2) visuals (profile + full overrides: background source/linkage/interval/Ken Burns, foreground inserts; target duration) — including an **I supply the background myself** toggle, and one for the inserts, whose meaning follows the source picked above it: a stock source becomes a search slopgen briefs you for, an AI source becomes a generation slopgen writes the prompt for, 3) ads (a saved contract *or* fully manual fields), 4) publishing (account, count, subtitle style, clean-subtitles switch), 5) summary with the equivalent CLI command, the breakpoint switches and the GENERATE button. *Drama* adds a **Story** step (plot + a reorderable cast, edited on the right, with photo→appearance vision and AI cast-fill), puts clip length, the speech-rate slider and visual constraints on **Content** and a parts field on **Publishing**, and turns the Visuals step into **orchestration** (an ordered list of AI generators; see below). *Fandom* replaces Story with two steps of its own. **World** holds everything that belongs to the place: which fandom, its characters listed on the left and edited on the right, and its lore documents edited in place — markdown source while typing, rendered and scrollable behind the ✎/👁 toggle — with the canon sheet under them and a Recompile button. A character here is anything the world can put on screen and needs drawn the same way twice: a person, but equally a creature, a machine, a vessel or a place that behaves like one — nothing assumes a face, from the card in the list down to the prompt that compiles its look. Nor does anything assume there is only one of it: each entry says whether it is one specific character, many identical faceless ones, or a whole kind, and that is the only field besides the name and the looks — a world's character is a LOOK, and personalities belong in the lore next to it. Each one is a file in the world's own folder: 💾 writes it into the world, 🗑 takes it out. A character is in the world or is not, so there is no adding one for a single video and no separate cast for the run — the pipeline reads them straight off the fandom. **Brief** is then this video: who is telling it (resident or chronicler), what it is about or which theory it argues, and an AI helper that writes that brief from the world and touches nothing else. The helper reads the records themselves rather than the compiled sheet (one line per thing is exactly where two similarly-named institutions stop being distinguishable), it knows how long the video runs — including that nobody has said, in which case its brief is what decides — and your instruction sets the shape and the length of what it gives back, with no cap: ask for a subject and you get two sentences, ask for the whole thing written out and you get the whole thing, which the script stage will then read as the piece rather than as a topic. Its fourth step is not the drama's chain editor either — it asks **where the shots come from** (🤖 AI generation with a generator picker, 🙋 I generate them myself, 🔍 I find them myself) and builds the one-stage chain behind the answer. The world comes first on purpose, and the wizard refuses to walk past World until one exists. Set everything up, press it, walk away.
- **Review** — where a run parked itself: the breakpoint screen (cards on the left, the open card's fields on the right, an AI edit line under them) or the gather screen for operator-supplied footage. The gather screen draws a **search** task as what it is — an errand, not a prompt to paste: its header says *find it yourself* and whether a still or a clip suits the moment, next to the seconds the shot runs; under it comes the brief, and under that the queries, one per line so they can be copied one at a time until one of them hits. The list marks each search row `[photo]`/`[video]` too. A generation task stays a single block of prompt. Both screens resume the run when you are done; `slopgen review` / `slopgen gather` open them directly.
- **Progress** — while a run works, a bar tracks the stage it is inside: voiced fragments, generated video fragments, assembled scenes and rendered files, each as `done/total`, over a per-video queue table and a live log. **The browser now shows the same strip**, in the run's own row: the stage by name, the tally, and the bar. The tally had been on the wire all along (`Run.progress`) with nothing to hang it on, because nothing recorded WHICH stage was counting — so a run in the browser was a status word and a log, and the only way to tell a slow stage from a wedged one was to read the log. The stage is now kept on the run and pushed to whoever is watching, on the stream that already carries the log but under a stage name of its own, so a tick moves the bar without being written into the log — and without a drama's eighty-four generated clips spending the whole event backlog on their own progress. Repeated ticks are dropped, a stage that starts empties the bar so a finished tally never sits under the next stage's name, and a stage with nothing countable in it (the script, the metadata) gets its name and a travelling stripe rather than a fake percentage. The strip exists only while the run moves: a full bar over a failed run would be a lie told by furniture.
- **Configuration** — sections on the left: LLM profiles (profile tabs, per-provider model presets, API-key input auto-saved to `.env`, ★ activation), footage/generator keys, the character library, the fandoms (world settings, its lore documents in the same editor, its own cast), ad contracts, accounts, presets. Entity sections have a tab per existing config file on top plus `+ new`; forms are prefilled, with 💾 save and 🗑 delete (confirmed).
- The chosen color theme persists across runs (`[ui].theme`).

## Telegram bot and Mini App (`slopgen bot`)

```bash
pip install -r requirements.txt         # nothing new; the bot rides on httpx
# 1. make a bot with @BotFather, put the token in .env:
#      TELEGRAM_BOT_TOKEN=123456:AA...
# 2. (optional but wanted) install cloudflared — it is what gives the Mini App an
#    https address:  https://github.com/cloudflare/cloudflared/releases
slopgen bot                             # foreground
slopgen bot --detach                    # ...or in the background, terminal free
slopgen bot --status                    # running? where is the panel?
slopgen bot --stop
```

Message it once and it replies with your own Telegram id; put that id into
`configs/bot_allow.txt` (one per line, `#` comments, the **first** id is the owner) and
it starts talking to you. The file is re-read whenever it changes — adding somebody is
an edit, not a restart — and **nobody who is not on it is served**, in the chat or in
the Mini App.

**The Mini App is the browser UI, not a copy of it.** The button opens the very same
page `slopgen web` serves, inside Telegram, on a phone. Signing in needs no password:
Telegram hands the page a signature only the bot's token can produce, the server checks
it and then checks the id against the same allow-list. One consequence worth knowing:
when the bot is serving, the panel has no anonymous door any more, password or not.

**The address is free and disposable.** Telegram opens a Mini App only over HTTPS, which
a machine under a desk does not have — so the bot raises a Cloudflare quick tunnel
(`cloudflared tunnel --url`) and takes whatever `*.trycloudflare.com` name comes back.
It changes on every restart, and that costs exactly one message: the button is rebuilt
from the current address, and the owner is told when it moves. Own a domain? Put it in
`[bot].public_url` and no tunnel is raised.

**Most of it works in the chat too**, and one part belongs there. Start a run by picking
a mode and a world (`/new`) — every other setting is what your configs already say,
because the parameters are built by the same code the browser's forms go through. Watch
them with `/runs`, stop them, release a breakpoint, and get the finished cut **sent into
the chat** as a video. Above all, hand material over: this pipeline asks a person for
pictures at unpredictable hours, and a parked run posts each shot it is owed as its own
message — reply to one with a photo or a clip and it lands in the run's inbox under that
shot's id, exactly where `slopgen gather` and the panel's upload put it. Send it as a
*file* rather than a photo and it keeps its full quality.

| `[bot]` in `configs/slopgen.toml` | what it does |
| --- | --- |
| `token_env` | which env var holds the token (default `TELEGRAM_BOT_TOKEN`) |
| `allow_file` | the guest list; first id is the owner |
| `web` | serve the panel in-process — this is what the Mini App opens |
| `tunnel` | `cloudflared` or `off` |
| `public_url` | your own https address; set it and no tunnel is raised |
| `deliver_video` | push a finished cut into the chat (≤ `max_upload_mb`, Telegram caps bots at 50) |

## Deploy to a server (`./deploy.sh`)

A Debian box with systemd, one script, no container.

```bash
cp deploy.env.example deploy.env        # SSH_HOST and SSH_USER, that is all
./deploy.sh bootstrap                   # packages, service user, venv, cloudflared, unit
./deploy.sh setenv TELEGRAM_BOT_TOKEN 123456:AA...
./deploy.sh allow 123456789             # your Telegram id
./deploy.sh restart && ./deploy.sh url  # where the panel is now

./deploy.sh push                        # a new release, switched only if it imports
./deploy.sh rollback                    # back to the previous one
./deploy.sh status | logs | pull        # what is up · journal · fetch finished videos
./deploy.sh run -- --list-types         # run the CLI on the server, same configs
```

A push does not copy files over running code: it builds the release beside the live one,
checks it imports in the venv that will run it, flips the `current` symlink and restarts
— and if the service does not come up, it flips back and restarts again in the same
breath. Rollback is therefore a symlink and not a backup you do not have.

State never travels with code. `configs/`, `.env`, `assets/`, `output/` and `models/`
live in `/opt/slopgen/.state` and stay the server's: the guest list, the bot token and
the finished videos were edited *there*, and a deploy that overwrote them would be a way
to eventually switch the bot off. `./deploy.sh seed` pushes your local ones over the
server's on purpose, by hand, and is the only thing that does.

## AI drama (`configs/characters/`, `configs/orchestration/`)

A second mode: a **narrated web drama** — one voiceover narrator tells a story (and may quote characters' lines inline) over AI-generated shots featuring a recurring cast.

- **Cast** (`configs/characters/*.toml`): `name`, `age`, `appearance`. Before generation each character is compiled once into a token-dense English `visual_prompt`, which is substituted **in place of that character's name** wherever the shot description mentions them — names never reach the generator (it cannot map a name to a face, and a foreign name gets rendered as literal text across the frame), and binding the description to the person doing the action keeps two characters in one shot from being blended or swapped (a text-only anchor — free generators won't lock a face perfectly). In the TUI you can build an ad-hoc cast, pull members from the library, upload a photo (vision → appearance), and let the AI fill the whole cast from the premise.
- **Visual registry** (the `entities` stage): the cast pins how *people* look; nothing pins anything else a story reuses. Named once in one shot and once in another, a transforming robot-house, a particular car or the kitchen everything happens in is drawn from scratch every time — which is how "robot-house" comes back as a plain robot. So once the script exists, a pass over every shot prompt collects whatever appears in **more than one shot** and is not cast, and describes it once. It is deliberately **untyped**: the model decides what is worth pinning — a machine, a building, a prop, a recurring person who never made the cast, even a crowd when it is a specific one (uniforms, placards) rather than passers-by — and the `kind` it writes is a label for you to read, nothing branches on it. Each entry's descriptor is then substituted in place of its name exactly like a character's. The same pass makes the prompts usable: a shot showing a registered thing must call it by its registered name, every cast member the writer listed as present must actually be *named* in the prompt (an unnamed one cannot be substituted into, and the look ends up appended loose, bound to nobody), and every prompt must say **where** it happens — a prompt that is only people and a verb renders as those people standing in an empty room. Review and edit the whole registry at the `entities` breakpoint; one descriptor restyles every shot showing it.
- **Prompt budget**: appearance must not swamp the shot. Three characters at ~20 descriptor tags each leave the action as a rounding error, and the generator draws three people matching their descriptions with nothing left for what they are doing. So a shared frame splits one appearance budget between the people in it (two get 6 tags each, three get 4, with a floor of 3 that keeps a face recognisable between shots); a lone character is never trimmed, and neither is a registry entry, whose descriptor is the only thing saying what an invented compound looks like. A character the shot mentions only as an owner (`Игнат's robot-house`) is not in the frame and contributes no look — otherwise a man with a tool belt gets glued onto a building.
- **Orchestration** (`configs/orchestration/*.toml`): an ordered list of AI generators, each a `model` (`wan2.1`/`ltx-video`/`animatediff` video, `flux`/`turbo` image), a `key_mode` (`rotate` keys on a limit / `single` key then skip), a `metric`+`amount`, and an optional `clip_seconds`. The pipeline walks the stages in order and each makes its share of the clips: `percent` = a share of the length budget, `seconds`/`clips` = an absolute chunk, and the last stage fills the remainder. Multiple API keys (one per line in `.env`) are rotated across stages. A stage's `clip_seconds` overrides the run-level average — handy when one stage's clips are longer than the rest (hand-made Kling/Veo shots next to 5-second Space clips). One `model` name is **not a generator**: `manual` — you make each clip by hand. It takes its share exactly like a generator, so a chain can send the opening minute to Wan and the rest to you, or mix found footage into a generated drama, without the share/ordering machinery knowing the difference. In the TUI they read as 🙋 you generate it and 🔍 you find it.
- **Length, parts & sync**: authored in **minutes** + a **tolerance** in seconds (the story may run a bit over/under). If parts >1 the **cuts are planned up front**, as beat numbers, by the outline pass below — and the window that owns a cut is told which of *its own* beats ends an episode, so that beat is written as a cliffhanger and the next one opens on the fallout. Which episode a beat lands in follows from the plan, not from a label the writer guessed at. **Clip length** is authored too (`--clip-s`, or the field on the wizard's Content step; 0 = each generator's nominal ~3-5s). It decides how many clips the budget is cut into and how much narration each carries — a 7-minute drama is 84 clips at 5s but 28 at 15s. The length is **fixed** — the writer never changes it, it only writes to it. What varies is how much story a beat holds: a drawn-out moment is told to run across several consecutive beats rather than being crammed into one, and a fast turn to be compressed into a single beat. Padding a beat with three unrelated actions to fill the time is exactly what reads as monotonous. The script is written a **window** of ~14 beats at a time, not all at once: asked for a whole feature-length script in one response a model spends its attention front-loaded, tracking the premise sentence by sentence at the start and turning to summary after — dropping named props, sub-plots and reversals you wrote down. Windows alone were not enough, and this is where a long drama used to come apart after the middle: told only that its beats sit *about 55% of the way through the premise*, a window has to eyeball which sentences of a two-thousand-word brief that is, and it eyeballs badly — the middle windows re-tell what an earlier one covered, skip what lies between, and the back half runs on what the model remembers instead of on your brief. So the brief is cut up **first**, by an **outline pass** that reads all of it in one call: per window it fixes what happens in that stretch, the checklist of concrete details from your brief that stretch owns and no other may spend (names, numbers, props, places, spoken lines), and where the story stands when it ends. Each window then writes to its own stretch, with the whole outline in front of it so it can see what is already told and what the windows after it are waiting for. The same pass chooses where the episodes are cut. Windows are balanced, so 30 beats are 15+15 rather than 14+14+2, because a two-beat last window is a whole call asked to write the ending in two beats; a single-window drama sees everything at once and skips the outline, and so does a run whose outline comes back unusable — it falls back to the percentage slices. Note that more *minutes* alone does not buy more detail: what a beat can hold comes from **clip length**, since narration is sized per beat (6s ≈ 12 Russian words at the natural speed, one short sentence). A detail-dense plot wants longer clips, not just a longer runtime. **Voicing speed** (`--tts-rate`) is part of that arithmetic rather than a cosmetic afterthought: a beat voiced at +30% takes a third more words to fill the same shot, so the writer is told the budget at the speed the run will actually speak, and the voice/picture fitting below is left with only the residual mismatch. Any single fragment can still be re-voiced at its own speed at the `tts` breakpoint. Whatever the length, a beat is ONE continuous take that moves, never a list of cuts: told `wide shot THEN close-up THEN reaction`, real generators read that as a storyboard and open the clip with every shot on screen at once, as a split-screen grid. Every prompt also carries an explicit single-frame clause, because generators reach for that layout unprompted. One beat equals one clip, and the two are fitted to each other in stages, cheapest change first. The **voice** moves before the picture does, since nobody sees it happen: up to ±25% it absorbs the mismatch alone and the clip plays untouched. Past that the voice sits at its comfortable edge and the **picture** slows to cover the rest (down to 45% speed — that reads as deliberate slow motion); only when the picture is spent as well is the voice pushed to its hard limit. A 20s line over a 15s clip is voice-only; a 45s one becomes a 1.35× voice over a 0.45× clip, still matching exactly. The other direction is not symmetric: a voice **shorter** than its clip is left alone and the surplus picture is simply cut off, which costs nothing and shows nothing — retiming there would buy a sluggish voice or a comic clip and still trim afterwards. Loading it all onto the voice is what used to leave the clip short, and a short clip **restarted from its beginning mid-scene**. Subtitle timings are rescaled to match — audio and video stay locked. A native ad, when enabled, is woven into the plot at the script level rather than bolted on.
- **Your plot, split — not rewritten**: what the writer is allowed to do with `--scenario` depends on what you put in it, and it is told so explicitly rather than left to work it out. Where the brief is **already written** — finished sentences, in your words, carrying their own content — the job is to **cut it into beats and nothing else**: your wording, your word order, your terms and names, your jokes, your order of events. Nothing added — not a cause, not a consequence, not a reaction, not an adjective — and nothing dropped. Where something is **actually broken** (it contradicts itself or the cast sheet, a name is used two ways, a sentence cannot be spoken aloud) it fixes that one thing, as small a fix as it takes, and leaves everything around it alone; a sentence plainer than a model would have written, a repetition, a blunt transition, a detail left unexplained or an ending that does not resolve are **not** faults and are not touched. Only where the brief is **notes** — a subject named, a list, a stretch it steps over — does it invent, and there it invents freely. The three cases are judged sentence by sentence, because a plot is usually finished prose in one paragraph and a bullet in the next. If your brief fills less than the running time, the extra goes into playing out the moments it stepped over, never into widening the sentences it did write — and if you want the story invented, write less of it, not a longer instruction. This binds the outline pass as well as the writers, so a stretch is never *planned* around an event you never wrote. It holds in the fandom mode identically, where the world's records are a constraint on how your brief may be told rather than material to fill its gaps with.

- **One episode at a time**: a part is a publishable video of its own, and the pipeline finishes it as soon as it can rather than waiting for the whole drama. This matters on the user-assisted path, where you make the clips by hand in some web tool and the free daily limits run out long before the story does: gather episode 1's clips, resume, and it is cut, subtitled, described and published while episodes 2 and 3 sit untouched. Come back tomorrow with more clips and `slopgen gather` picks up exactly there — the parts already done are never re-cut and their clips never re-generated. Each part gets its own timeline (its subtitles start at 0:00), its own file (`part_02.mp4`), its own `metadata_part_02.json` written by a model that is told which episode of how many it is describing, and it goes out the moment it is ready. An upload is recorded on the part itself, so no amount of resuming can publish it twice. The gather screen shows a **part** column and, per episode, either `part 1 ✔ ready to cut` or how many clips it is still short — **Finish & resume** needs one complete episode, not all of them. Nothing stops you carrying on: while a part renders you can keep generating clips and dropping them into `manual/inbox/`, and the next resume finds them. If you would rather have the whole drama land together — or watch all of it before any goes out — turn the **Finish parts one at a time** switch off (`--parts-at-once`) and nothing is cut until the last clip is in, which is how it worked before.

- **Censorship & framing**: two switches that keep a run publishable. **Visual constraints** (`--visual-notes`) bind what the shots may SHOW and nothing else — the story is written as if they did not exist, so "all weapons are toy ones" leaves the gunfight a gunfight and only changes the props; they reach the writer and ride along on every generated prompt. **Clean subtitles** (`--clean-subs`) swaps profanity out of the burned-in text while the voiceover keeps every word — platforms moderate what they can read. Whole lines are rewritten, not single words — «Съебал нахуй с моей пары пидорас блять» becomes «Уйдите пожалуйста с моей пары молодой человек», where word-by-word swapping would leave a limping sentence. It catches words that merely look profane, such as the first part of the name «Хуй Сунь Вынь», and since a rewrite changes the word count, the line's span is re-divided among its new words so it still starts and ends with the speech. Cost is bounded: lines are screened by regex first, so a clean video makes no request at all, and only the flagged lines travel (each with its immediate neighbours for context) in a single request per video. Replies are keyed by line number, so a partial one still lands what it returned and only the rest fall back to masking.

Run it from the TUI (Generate → AI drama) or headless: `slopgen drama ru --scenario "…" --cast example --duration-min 2 --tol 20 --parts 3 --orchestration my_chain`.

## Fandom (`configs/fandoms/`)

A third mode. A **fandom** is a fictional world you write down in markdown; the scriptwriter then narrates a video set in it, **treating that world as the real one it lives in** — never as fiction it is describing. That posture is the entire point of the mode, because the default is the opposite one: handed a world document, a model *explains* it. "In this universe…", "the author never clarifies…", "fans have long theorised…", "unlike our world…" — and what comes out is a video about a document instead of a video from a place. So the prompt contract takes that posture away and keeps taking it away, clause by clause, since each way of breaking the illusion is its own reflex: naming the medium, naming the author, addressing an audience of fans, or reaching for our world as the reference frame. There is no outside to compare this to. And where the records are silent the world is not — something *is* true there, it is merely unknown, disputed, forgotten or deliberately unrecorded, which is the difference between "the ledgers disagree" and "the lore is inconsistent". A gap in the lore is a gap in what is **known**, never a gap in what somebody wrote down. Whatever the writer invents to close one has to be the kind of thing this world already contains — no object, word or institution its records give no reason to believe exists.

- **How far it may invent — a three-position slider, next to the narrator.** `never` (the default): the records are the whole world, and where they stop the piece says so — nobody knows — and puts nothing of its own in the hole. `only where stuck`: it invents, but as a repair rather than a licence, only where the beat in hand genuinely cannot be written otherwise, and only the smallest ordinary thing that unblocks it — a habit, a price, the order things are done in — said in one clause, in passing, with nothing later in the piece leaning on it. `freely`: the records are merely what somebody wrote down, and the world is furnished at will in the same grain. Thin lore is unwritable under the first; a world you are genuinely archiving is ruined by the third; most worlds want the middle one, which is why this is a slider and not the checkbox it started as. **The limits are the same in all three positions**, because they were never about how *much* is invented: never a contradiction of a record, never a thing outside what this world is made of, never the **subject** of the piece (the place, the custom, the person the video is *for* comes from the records, always), never a **coined proper noun** (naming a thing is the strongest claim a narrator can make about it, and a coinage is heard as the world's own word and cannot be told apart from one afterwards), and never texture laid down beside an open question — that reads as a hint at its answer. Told merely that it *may* invent, a model does not add a lantern-maker's price: it invents the thing the video is about, or names what the records deliberately leave open. That is what the two-position version actually did. **And your brief cannot create anything either**: it decides what is *talked about* and has no power to bring a thing into existence, so a brief naming something the records do not hold is answered by finding what it was pointing at and writing about *that* in the world's own word for it — or by saying plainly, inside the world, that no such thing is known here. Ask a mountain post-station about «the Object» and you should get the place under the ninth marker by its real name, not a coinage.

The `--scenario` brief here answers a different question than the drama's: not *what happens*, but **what about this world to tell** — a custom, a place, a person, an unexplained event — or **which theory about its records to argue**. Those two want opposite handling, and the writer is told so: asked for a theory it must actually build one, lay out the evidence, name what does not add up and commit to a conclusion, rather than summarising the evidence and trailing off. It is a claim made inside the world by someone who lives there, never a fan theory about a text.

- **The folder is the world** (`configs/fandoms/<name>/`): `fandom.toml`, one or more `.md` lore documents, and `characters/*.toml` — the world's own characters, kept here rather than in the global library because they are part of the world: a run set in it gets them without you naming a single one, and they never clutter the list the other modes pick from. A fandom is a **directory**, unlike every other config kind, and the folder name is its identity whatever the TOML says. The TOML is optional — a folder holding nothing but markdown is a valid fandom with every setting at its default. Its keys: **`docs`** = the documents in reading order (empty = every `*.md` in the folder, sorted by name; a name pointing at nothing is skipped rather than taking the whole world down with it), **`tone`** = a free-text note on register and delivery, handed to the writer as-is, **`lore_tool`** = whether the writer may query the full lore (below). **`canon`** and **`docs_sha`** are machine-written — the compiled sheet and the checksum it was built from; don't hand-edit them. `configs/fandoms/example/` ships a small world (a mountain postal station, its customs, its chronicle and five people) to read as a template. It deliberately ships WITHOUT a compiled `canon`: generated output checked into source drifts from the code that generates it (this one already had, still carrying a section heading the compiler has since renamed), and the compile is a one-time cost per world that is cached from then on; everything else under `configs/fandoms/` is git-ignored like the rest of the personal configs.
- **A character here is a LOOK, not a person.** The drama invents its cast, so a character there is somebody: a name, an age, a personality the drama is free to write. A world already has its people, and what it needs written down about them is the one thing prose cannot hand to an image generator — what they look like. So a character in a fandom is a face, a coat, a hull, a silhouette, and **nothing else**: no age field, and no personality field at all. Who someone is, what they did, what they are like and what anyone thinks of them goes in the **lore documents**, in prose, together with the weather and the ledgers — that is where the writer actually reads it, and where you will find it again in six months. Or it goes nowhere, which is equally fine: plenty of what a world puts on screen has no personality to write. Two consequences follow. A character need not be a **person** — a creature, a machine, a vessel, a building the story keeps returning to — and nothing along the way assumes one: not the card in the wizard, not the AI that fills a character in from the lore, not the compiler that turns `appearance` into a generation prompt, which for something with no face describes form, material, scale and markings instead. And a character need not be **one of anything**. An entry is marked as one of three things, which is the only structured field besides the name and the looks: **one specific character** (this face, in every shot it appears in), **many identical, faceless** ones (the wardens, the carriers — one look worn by all of them, none of them named), or **a whole kind** (a race, a caste, a model of machine, described by what any member shares). The mark earns its place twice: unmarked, a group reads to the writer as an individual and quietly acquires a name, a line and a personal arc, and the compiled prompt — which is substituted in place of the name in every shot — must be one *singular* figure carrying nothing personal, or "three wardens" renders as three men with the same scar. Say "three ⟨name⟩" in a shot and it holds three of them; the name itself never changes.
- **The canon sheet**: the lore is compiled **once**, by an LLM, into a dense reference — what this world is, how people here talk, the rules a story set here may not break, the taboos, the glossary, the figures, the places, the factions, the timeline — and stored in `fandom.toml` as `canon`. This is exactly what a character's `appearance` compiling into `visual_prompt` is, and for the same reason: the writer needs the world as something it can hold in front of itself for every one of ~18 script windows, and eighteen readings of your raw prose costs eighteen readings while spending the model's attention on narration it does not need. The sheet is an **inventory, not a retelling** — every name, number and date in the records' own spelling, one line each. Its **taboos** section is the one part that is inferred rather than copied: a world of footpaths and lanterns has no cars and no telephones, and that list is what stops the narrator furnishing your world out of ours. Freshness is a **checksum of the documents** rather than a dirty flag, precisely because lore is comfortably written in whatever markdown editor you like, where nothing in slopgen is watching and nothing would raise a flag; what triggers a rebuild is the text on disk no longer hashing to `docs_sha`, so a rename, a reorder or a deletion invalidates the sheet exactly like an edit does. Saving lore in the TUI rebuilds it there and then (the file is written first and the sheet compiled after, so a failed LLM call costs the sheet's freshness and never your text), which leaves the run's `canon` stage as a lazy guard that in the ordinary case makes no call at all. A rebuilt sheet is written straight back into `fandom.toml`, so the next run — and the TUI — gets it for free, and it also rides along on the job, so a resumed run keeps writing against the world as it stood when the script was started (re-planning half a script against edited lore would contradict the half already written). Under **4000 characters** of lore (`SMALL_LORE_CHARS`) nothing is compiled at all: the documents are shorter than the sheet would be, so the writer is simply handed them whole. Two things that share a NAME are two entries, and the compiler is told so outright: where two sides each keep an office, a body or a custom of the same name — hunters who work one way here and another way across the border — folding them into one line puts one side's practice on the other as fact, and nothing downstream can tell that it is wrong. The checksum carries a **compiler version** alongside the documents, so improving that prompt retires every sheet already on disk instead of reaching only the worlds whose lore is edited afterwards; the next run rebuilds them, one call each, and the TUI flags them as stale meanwhile.
- **Why three layers.** The world reaches the writer three times over, cheapest first, because no one of them works alone. **(1)** The canon sheet, in every window — an inventory, so the writer knows a thing *exists* even when it would never have thought to ask. **(2)** The **outline pass**, which reads the whole lore in one call and hands each stretch of the video the concrete facts that stretch is responsible for spending: names, dates, numbers, places, customs, quoted lines, in the records' own wording. It is the only pass that reads everything, so a detail it does not hand out is a detail the video loses — which is why it is told to be generous, and to put each detail in the one stretch it belongs to and nowhere else. This is the main channel through which the world's real texture reaches the page. **(3)** `lore_lookup` — an archivist: a librarian LLM that reads the whole document to answer one question, offered to the writer as a real function call for the detail it *knows* it is missing before it commits to a name or a date. Layer 3 alone would fail twice over: a writer never asks about the currency it does not know exists (the classic retrieval failure, which layer 1 fixes by being an inventory), and every call re-reads the whole document, so ~3 questions across ~18 windows already costs more than pasting the lore into all 18. Hence last resort, not only channel — and switched off (`lore_tool = false`, or automatically when the lore is small enough to be pasted whole) when there is nothing in the records the sheet does not already hold. The archivist speaks from inside the world too: told the records are silent it says so as silence in the *world*, never "the author never specified", which would hand the writer back the one framing the mode exists to remove.
- **The spine, and why a short piece needed one.** The three layers put the *world* in front of the writer, and not one of them says what the *video* is. Measured, on a thirty-second run with the brief «Первый день на Объекте»: three beats came back, every one faithful, in the world's own words and unimprovable line by line — a checkpoint and a form, then a briefing paper, then a piece of advice about a tally mark. Three true things about one subject with nothing following from anything, which is exactly what jumping from subject to subject is. No rule in the mode was broken, because no rule in the mode was about the piece. The outline pass is the only planning here and it runs above fourteen beats and nowhere below, so every short video had no plan at all and got the drama's arc (hook → rise → turn → payoff) offered to a piece with no plot in it — while `FIDELITY_RULE`, reading a four-word brief, called it a sketch and handed the writer a free licence over all three beats. Free licence, no plan, no shape: three stabs at the topic is the only thing that could have come out. So what a piece of this kind *is* is now written down. It is one thing, opened in the middle of itself, taken apart in an order where each sentence is caused by the last, turned once near the end when the arrangement turns out to have a price, and stopped on a line that explains nothing. That is `PIECE_RULES` — five rules put as tests a writer can actually apply rather than tastes it cannot (*lift a beat out and drop it into a different video about this world: if nothing notices, it did not belong in this one*; *swap any two beats: if nothing breaks, you wrote a list*) — and it is decided per video by a **spine pass**: one call, before a single beat, that reads the whole records against the brief and answers with the subject in the world's own words, which of four shapes it takes (**mechanism** — one arrangement taken apart, what it costs the people it is done to; **duties** — somebody has been put somewhere and this is what that means for them; **rule** — the conditions it holds under and what reaches you when it does not; **vignette** — one named someone wants one concrete thing, and it ends unfinished), the fact the piece opens inside, three to six ordered steps each following from the one before, the turn, and what the last line does. The writer is handed that as its order, and told that the steps are the order of the piece and not its beats. The pass also gives the short videos back the layer they never had — below fourteen beats it is the first thing in the mode that reads the records whole. A brief over 600 characters gets no spine: the operator has written the piece, and a written piece is cut up rather than re-planned. A spine that comes back unusable is not fatal — the piece is written under the five rules alone.
- **Your `tone` note binds both ways, and dryness has a cause.** The world's `tone` in `fandom.toml` is the one sentence you write about how the place sounds, and it used to arrive at the writer as a footnote under the canon sheet — with the result that the half of it agreeing with the length budget landed and the other half did not. A world asking for a voice that is even and clerical and speaks in short phrases, and in the same breath *readily drops in sayings and omens*, got the evenness and not one saying: exactly the dryness the note asked for and none of the texture it asked *for*. The note now has its own heading, is declared binding in both directions, reaches the spine as well as every window, and outranks the length budget — a beat that will not fit loses a fact, a step or a clause of business, never the register, because a beat stripped to its information is a notice board. Alongside it every beat is now required to carry one thing besides its fact: what the listener's hands or eyes are doing, what they are dreading or already spending in their head, a measure in the world's own units, a saying people here repeat, or one physical detail nobody needed to mention — your tone note decides which of those your world likes, not whether there is one. And nothing may be *always* or *as a rule* where it could be *this time*: a general truth about the subject («первый день всегда одинаков», in a piece about your first day) is the driest sentence available, and it stands the listener outside the very thing the piece exists to put them inside.
- **The plan, and the button that rewrites the script from it.** A fandom video is decided in six fields before a beat is written — what it is about, which form it takes, what it opens inside, the steps in order, the turn, what it stops on — and the `script` breakpoint now opens with them, above the beats they produced. Edit any of them and press **rewrite from the plan**: the run stays parked, the beats are written again from what you just wrote, and the document comes back with the new script under the same plan. That is the cheap loop. Editing six narrations into agreement with a plan you would rather have had is the expensive one, and it leaves a piece patched into looking right rather than one that came out of a plan you agree with. The form field is a dropdown of your shape catalogue, so "this should have been a choice, not a mechanism" is one field and one button. A plan emptied entirely is a plan removed; a plan written by hand into a run that never had one works exactly the same way.
- **Two narrators**, chosen per run (`--narrator`, or the World step in the wizard) — both of them inside the world, differing in where they stand in it. **`resident`** lives there: first person, the world as daily life, what they have seen and what everyone here knows and what nobody here can explain, other people's lines dropped in raw and inline. It is the drama's voice pointed at a world instead of a plot. **`chronicler`** studies it: an archivist, a researcher, a crank who has read too many ledgers, speaking about their own world's records the way a historian speaks about ours — dry, specific, with sources and dates and the parts that do not add up. It may say "I" about its own reasoning but it is not the hero of anything. Both are forbidden to open with a definition or address an audience unfamiliar with the world: everyone listening lives here too, and what they lack is not the basics, it is what you found in the records. Same for the first beat — it opens on a concrete moment, object or claim, already in the middle of it, never on a sentence that would only be written for someone who has never been here.
- **Where the shots come from** — one question, not a chain. The drama's fourth wizard step is an *orchestration*: an ordered list of generators, each taking a share of the video, each with its own key-rotation policy. That machinery exists for a reason — a feature-length drama burns through the free daily limits somewhere in its middle and has to hop services mid-run — and a fandom video is one piece of a few minutes, which needs neither the machinery nor the framing it puts around the work. So this mode asks the only question that actually matters, **Where the shots come from**: **🤖 AI generation** (with a generator picker next to it), **🙋 I generate them myself**, or **🔍 I find them myself**. The one-stage chain the pipeline runs on is then built behind you, because everything downstream speaks chains and this is the only place the simpler question has to be translated. The two operator answers park the run at the footage stage and hand you the shot list (`slopgen gather`): prompts to paste if you are generating, briefs plus ready-made queries if you are searching — see [user-assisted material](#user-assisted-material-you-generate-it-or-you-find-it).
- **Everything else is the drama's**, unchanged: the cast with its compiled visual prompts and its shared appearance budget, the visual registry (`entities`) pinning whatever recurs and is not a person, the beat machinery — outline pass, windows, the voice/picture fitting, `--clip-s` deciding how much story a beat can hold — AI clip generation and both user-assisted paths (make the clips by hand, or go and find them), ads, `--visual-notes`, `--visual-style`, `-F/--filter`, `--clean-subs`, and every breakpoint, plus one of its own. What it does **not** take is episodes: a serial is cut where it hurts most, and an account of a world has no cliffhanger to hang a break on, so there is no `--parts`, no parts field and no `cut` stage. Nor the drama's orchestration editor, for the reason just above. Nor does it take the drama's way of casting — see above. The generator, mind, has never heard of your world: a shot prompt may never carry one of its terms untranslated, so the writer describes what the thing *looks* like in plain English (not "the winter carry", but "figures in heavy coats carrying mail sacks single file along a snowbound mountain path") while the narration goes on calling it by its own name. The stage chain is `canon → script → entities → tts → picture → footage → subtitles → assemble → metadata` (`picture` plans the frame-base track and is skipped by a run whose shots come from a generator), and `canon` is a breakpoint like the rest: the last free moment to fix the world before a single line is written against it.

Run it from the TUI (Generate → Fandom) or headless: `slopgen fandom ru example --scenario "…" --narrator chronicler --duration-min 3 --tol 20 --parts 2 --orchestration my_chain`.

## Chat (`configs/chat/`, `configs/personas/`)

A fourth mode, and the first whose picture slopgen **draws itself**. A conversation — a group chat, a comment thread, something you typed out of your own head — is read aloud and rendered in a messenger's own interface, message by message, as it would arrive on a phone. Three interfaces ship: **Telegram** (light chat on a wallpaper, bubbles taking sides, the little spur at the corner), **Discord** (dark flat list, avatar and name and lines), **Reddit** (white thread, indented replies, a karma line under each comment).

- **Reddit does not mix with the other two.** A Telegram export rendered as Discord is an ordinary thing to want and nothing is lost doing it. Reddit is not interchangeable with either: its unit is not a chat line but a post with a comment **tree**, it indents instead of taking sides, and it scores with karma where the messengers react with emoji. Flatten a thread into a chat and half the replies answer nothing; send a chat the other way and every bubble sprouts a score that was never there. So the mode refuses the combination rather than producing a plausible-looking lie.
- **Who reads it, in three answers.** Each person's own card (`configs/personas/*.toml` carries the nickname, the picture, the colour and the voice), **one voice over all of them** (the common case — a single narrator reading somebody else's group chat, which is the format this mode exists for, and re-carding eight people to say so would be the settings eating the evening), or **nobody**. The last is a format, not a broken state: a conversation people read off the screen over music is a video. A message nobody reads appears whole and holds for exactly the pause.
- **A message arrives the way it is read.** A long one appears in pieces — `chunk` characters at a time, each piece landing on the first *word* of it, by the word timings the voicing recovered — so the bubble grows under the narrator rather than sitting there finished while he catches up. Below `chunk_min` characters a message appears whole, because a two-word line revealed in two pieces reads as a stutter. `gap_s` is the fixed pause between one message and the next, and it is also the whole length of a message nobody reads aloud.
- **When the screen fills**, three answers, and the third is also available to the other two. **Follow it** — the view travels up after the newest message, `roll_s` long, the way a real client scrolls. **Jump** — it moves in one frame. **Clear it** — the screen empties and the conversation starts again at the top. The clear is also a mark you can put on any single message, because a conversation that rolls throughout still wants a clean screen where *you* decide, and where that is depends on what is being said.
- **Reactions land when the message has been read**, one after another and faster the more of them there are — `react_s` is the whole flurry however many it holds, so eight reactions do not take eight times as long as one.
- **It translates itself, and knows when not to.** A found conversation is in whatever language it was had in. What makes this hard is not the translating: it is that a Russian chat saying `ну всё, я go sleep` must keep the `go sleep`, because the foreignness **is** the joke — and a translation API handed that fragment will faithfully render it, since it is being handed fragments and the judgement needs the whole conversation. So it goes to the model you already have configured, which sees every message and returns only the ones that genuinely needed it. The original is kept beside the translation, because reviewing a translation without it is not reviewing anything.
- **The typefaces are each client's own**, asked of fontconfig by name and never by path — a config holding a path works on the machine it was written on and, on NixOS, until the next update moves it. Telegram's and Discord's real faces are not redistributable, so what is installed is the open stand-in each skin asks for (Roboto, Inter, IBM Plex Sans) plus Noto Color Emoji; a machine without one draws the chat in whatever grotesque it does have, and the video is a little off rather than absent.
- **Avatars, wallpapers and send sounds are plain folders** — `assets/avatars/`, `assets/chat_bg/`, `assets/chat_sfx/` — the way `assets/music/` is one. A picture has no properties to write down, so a TOML per file would be upkeep in exchange for nothing. Nobody who has no picture is left blank: they get the initials disc every real client draws, in a colour derived from their name, which is also what an export gives you, since an export carries no pictures at all.
- **How many conversations go in one video** is a setting (`want`), because a video holding three chats is the format rather than an edge case — a source has to know how many to go and find, and the room simply holds however many you put in it. Each carries its own name on the header bar, and the swipe between them is drawn rather than cut.
- **9:16 or 16:9.** The frame is the run's, not the mode's, so turning it turns everything — the drawing, the subtitles, the delivery. On a wide frame the conversation is laid in a centred column with the wallpaper either side, and the glyphs keep the size they were designed at: scaling a 1920-wide frame by its width would fit three messages on screen, which is a chat nobody has ever seen.
- **A split screen**, for the people who need something moving in the other half: the chat above, a loop below — a file under `assets/footage/`, a folder of them, or whatever it rolls. It is seeked to where it falls on the clock rather than restarted, so the gameplay carries across every message; `split_change_s` rolls a different clip every so often instead.
- **A send sound**, from `assets/chat_sfx/`, once per message as it lands — not per piece of a long one, and not again when a reaction pops, because that is not what a phone does.
- **The settings are a saved preset** (`configs/chat/*.toml`), because there are thirty of them and the set that makes "a reddit story" differs from the set that makes "a group chat bit" in nearly every field. Being an ordinary named config it saves, renames, overwrites and deletes in the config panel like everything else. `configs/chat/example.toml` and `configs/personas/example.toml` ship as templates.

**The room is where the video is made.** Press **🎬 собрать вручную** on the start form — or **🗨 переписка** on a parked chat run — and you get the thing itself, in three columns. The **conversations** the video is made of, which you add to, name, reorder, cut in two and join back up; the **messages** of whichever is open, each carrying who sent it, what it answers, the reactions on it, the time on the bubble, a nickname and a picture for this one message, and a mark that clears the screen before it; and a real **frame** of what it will look like, drawn by the renderer that will draw the video. **Paste it in** takes a block of `Ник: текст` lines in one go, which is how a conversation copied from somewhere actually arrives; anything that is not such a line continues the message above it, so a message somebody wrote across three lines stays one message. **Who is in it** lists the participants with how many lines each has and who reads them, and one button gives anybody a card — a nickname, a picture and a voice that survive into the next video rather than living on this one job.

**Settings change with the frame in front of you.** The sheet in the room holds the skin, the chrome, the clock and the trimmings, and a change takes effect on the next drawing. They belong to THAT video: touch any of them and it stops following the preset it was started from, because an edit here would otherwise change every other video on that preset too.

**And there is a clock, once there is one.** When the conversations have been laid out and drawn, a strip appears under the frame with every message marked on it and every swipe marked brighter. Drag along it and the preview shows the frame the finished video shows at that second — the state that is up then, cropped where its window has travelled to by then, computed by the same arithmetic ffmpeg renders with. Before that there are no timings to show, so the strip stays away rather than inventing them.

The chain is a row of buttons across the top, pressed in the order the work takes rather than the order the list is written in: write, draw, look, change a line, draw again.

A reply points at a message by index inside its own conversation, so dropping or moving one carries every pointer with it — and a reply to a message you deleted becomes a reply to nobody, never a reply to whatever slid into that slot. Editing does not re-voice anything by itself: the timeline appears when you press **сценарий**, and the voicing reuses every take whose line you did not touch.

The stage chain is `source → script → tts → render → subtitles → assemble → metadata`. There is no `cut`: a conversation is one video, and the separate excerpts inside it are a thing on the screen rather than a thing to publish separately. `render` sits after `tts` because when a piece of a long message appears is a question about word timings, and those do not exist until something has said the words. Every stage is a breakpoint, and `source` is the important one: it is where a run parks with a conversation in front of you while you decide what the video is.

Start it from the browser (Generate → переписка). The length field is absent on purpose: a conversation runs exactly as long as it takes to read.

## The montage room

The one screen in the browser that is not a form — a workbench for one **fandom** video
whose pictures come out of the world's frame base.

**Getting in.** Fill in the fandom form and press **🎬 build it by hand** instead of
START: the run is made — a folder, a checkpoint, the settings — and *nothing is run*.
The room opens on an empty video. Starting the chain and stopping it at a breakpoint
cannot serve this, which is why it is a second button and not a tick: by the time a
screen appeared, the run would already have decided the things you opened it to decide.
The other two doors are for a run that is already going — the `picture` breakpoint, and
the same **🎬 montage** button on its row. **← back** leaves; the run is an ordinary
parked run in the list, with that button on it, and pressing it puts you back exactly
where you were. Nothing is held in the tab: every edit is already on the checkpoint by
the time the screen redraws, so closing the browser mid-montage costs nothing.

**Getting back in after it is cut.** A finished video is not a dead end. Its row carries
**🎬 cut it again**, and pressing it takes the *render* off — the picture track as it was
compiled onto the scenes, each part's subtitle file, each part's cut — and parks the
video, which is what makes it editable again: every
editing route in the room refuses a video that has already been cut, because otherwise
the file on disk would go on being the answer while the timeline under it said something
else. Nothing else comes off: the lines, the takes, the cards, the moves, the effects
and the look are exactly what you came back to change. Nothing is deleted either —
`assemble` writes each part to the same path it used before, so the old cut is
overwritten when the new one is made, and until then it is still there to be watched,
which is most of why anybody reopens a finished video. The room's watch button says
**watch the previous montage** while that is what it is playing. **Who cut it does not
matter**, and the unattended case is the ordinary one: that the matcher put the wrong
card under a line is usually only visible in the finished video, which is exactly when
the room used to be shut. What is left needing doing is three stages — footage,
subtitles and assemble, all three drawn un-done on the rail — and either way of
finishing rewrites the file in place: press them here, or press **build it and go on**
and let the chain do it. Footage is on that list for the reason the others are not
obviously missing it: it is the stage that lays the shots onto the scenes, and what
`assemble` renders is what it left there — leave those pieces in place and a re-cut
track changes nothing at all. Where the picture comes out of the frame base that costs
one walk of the track and asks no model anything.
Walk away instead and the run is honestly unfinished and looks it, rather than a finished
run whose file disagrees with its own timeline. The title and the upload are deliberately
left alone: a re-cut is a new *file*, not a new publication, so the metadata is not
rewritten and an episode that has already gone out is not sent again.

**The pipeline, à la carte.** Across the top is every stage of the chain as a button:
canon, script, entities, voice, picture track, footage, subtitles, assemble, metadata.
Press one and it runs — the same callable the orchestrator would have called, on the
same job, recorded in the same completed list, so a run driven by hand here can be
resumed by the chain afterwards and walks past what is already done. A stage whose input
is not on the job yet is disabled rather than hidden, because what is missing is the
useful thing to see. It reports where a stage always reports, the run's own log and bar,
because voicing forty lines is a minute in which this screen has nothing to say. The
order is yours: the work really goes write, hear it, look at it, re-write that line,
voice it again, cut, cast, cut again — and none of that is the order the list is in.
Nothing forces you to press **script** at all; type the lines yourself and press
**voice**.

Every press **asks first**, and the question is the point of it: a row of nine identical
chips says nothing about which one calls a model, which one is free, and which one
replaces the lines you spent an hour typing. So the sheet names the stage, says in one
line what it does, and lists what pressing it would spend and what it would write over —
*throws away and rewrites every line, the ones you typed included: 6* — with the button
painted red when that is what it means. The counts come off the job, so they are this
video's and not a general warning.

Work done by hand **counts as the stage that would have done it**. Type the script here
and `script` is marked complete, because its output is on the job however it got there;
the same for voicing, cutting and casting. That is not bookkeeping — it is what stops
the **resume** button on the run's row from walking into the writer and throwing every
typed line away, which is exactly what it did before the completed list was read off the
job instead of merely carried. Which also means resume is useful here: hand-make the
half you care about, and let the chain finish the rest.

And what it shows underneath is the video's own two clocks, laid over each other.

A picture track in this mode is deliberately **not** aligned to the narration: it is
cut where the speaker breathes, and that asynchrony is most of what makes a folder of
stills read as edited footage rather than as a slideshow. Reviewing that as forty rows
of `4.1–8.3s · push_in · рынок` is reviewing a film by reading its edit decision list,
and the two things you actually want to do to it cannot be typed into a row at all. So:

- **A cut is a word.** Click the word a shot should start on, and it starts there — the
  shot that was up ends at the same moment, because that is what a cut is. On a video
  with no track at all, the first such click lays one down and cuts it: an empty track
  is not an absence but one uncut shot per stretch, waiting to be cut, and the one
  gesture the screen is built around must not require some other button first. The
  other way in is **cut it on the speech**, which cuts the whole thing at once where the
  speaker breathes and costs nothing; both are offered in the empty lane, which is where
  somebody stands when they cannot work out how to make a shot. The **✕** on
  a shot's left edge takes that cut back and the shot joins the one in front of it. What
  is stored is the word and not the second (`FrameShot.anchor_word` has been there since
  the track was written), so a cut placed by hand survives everything that moves the
  clock afterwards: re-voice a line at half speed and every cut after it slides with the
  words it was placed on, rather than landing half a second off the word you meant.
- **A cut is dragged, and it snaps to words.** Take a shot's block and pull it along the
  track: it does not go where the pointer stopped, it goes from one word start to the
  next, and a dashed line runs from the block down to the word it would land on with
  that word lit. Free dragging would be a second, worse answer to a question this
  screen has already answered — and it would answer it in *seconds*, which is the unit
  that does not survive a re-voicing. Dragging is one operation rather than *take the
  cut off and place it again*, because the two-press version loses the shot in between:
  ✕ folds the stretch into its neighbour and the card, the camera move and the pin go
  with it. Words a cut may not land on are simply not offered, so the drag never stops
  somewhere that answers with a red line — a word another shot already starts on (two
  shots on one moment is one shot), the first word of a region (the shot it opens with
  is right in front of it), anything inside an ad. The shot a region opens with has no
  cut to move and does not take the grab cursor at all.
- **The video opens with a shot, always.** The first one on the track is not a cut
  anybody placed — it sits at 00:00.000 and it is simply where the video begins — so it
  cannot be taken off the track: **✕** on it takes the *picture* off and leaves the shot
  standing, empty, over its own seconds. It used to be removable, because the only thing
  that marked it out was its start, and a shot cut on the first WORD starts a few tenths
  in — the speaker breathes first. Remove that and the track began three seconds into
  the video: nothing owned the opening seconds, the render silently handed them to the
  neighbouring picture, and the screen could not repair it, because the one gesture it
  has is to cut the shot that is up and over those seconds there was none. The opening
  shot is now put back wherever it has gone missing, including in runs saved before this
  was true, and the same holds for the stretch after an ad — every region opens with a
  shot of its own. Clicking the first word of a region is answered with that shot rather
  than a quarter-second sliver in front of it.
- **The voice and the text are one track and two edits.** A line's **text** is what is
  read — the burned-in captions are built from its word timings — and saving it re-lays
  the new words over the span the old ones occupied, touching neither the audio nor the
  clock. A line's **voice** is what is heard and is what the clock is made of, so
  re-voicing it (at the run's speed, or at its own, on the slider beside the button)
  changes its length and moves everything after it. You can also read the line yourself
  and hand over the recording: the timings come back from the recognizer, the same road
  `--tts-source manual` takes for a whole video. Lines are **added and dropped** here
  too, which is not true of the drama and is worth saying why: there a beat *is* a clip,
  so adding one means a shot to generate and a sync to redo for everything after it —
  while here the picture track does not know the beats exist. It is cut on words and
  runs past the narration on purpose, so a line put in costs exactly one thing: the shot
  straddling the seam grows by its length, and you cut it if you would rather it did
  not. Every other shot keeps its card, every cut keeps its word, and nothing already
  voiced is voiced again. A new line arrives silent and takes up no time until you voice
  it — and until then it is listed in the **no voice yet** strip under the track, because
  a clock has nowhere to draw a line with no length: a dozen of them share one second and
  collapse into a sliver nobody can hit. The text box keeps a draft that survives a
  redraw, a caret, and selecting another line and coming back; **leaving the box saves
  it**, and so does anything that moves the lines underneath it — pressing ＋, voicing,
  running a stage — because a draft is held by line number, and losing what somebody
  typed to an index shift is not a trade worth making.
- **Lines are reordered by dragging them**, by the head strip of the block — the body
  below it is full of word chips and a word is the cut gesture, so the one strip that
  belongs to nothing else is the handle. The block follows the hand and a heavy line
  marks the seam it would land at; it snaps, because the lines are a sequence and the
  only thing a drop can mean is which two it lands between. This was refused for a long
  time, on the grounds that a reordered script is a track to be cast from scratch, and
  that was a misreading of the room's own design: a cut is a WORD and so is an effect,
  and the words travel with the line they are in — so the anchors are re-numbered through
  the move and every cut comes out on the syllable it was placed on, still carrying the
  card that was chosen for those words, with the arrow still on the thing it pointed at.
  Nothing is cast again. What a reorder really costs is the **length of the pictures
  either side of the seam**: a shot runs to the next cut, and moving a line moves cuts
  past each other, so a shot whose neighbour has just travelled to the far end grows to
  meet whatever is now in front of it. That is honest arithmetic — the seconds have to
  belong to somebody — and it is the thing to look at afterwards. The takes are renamed
  to their new positions as part of the move, which is the half that would otherwise
  corrupt audio: `tts` names a take by its line's index, so a permutation left alone
  leaves every one of those names pointing at somebody else's voice.
- **A pause is a thing on the track.** **＋ a pause** puts a stretch of silence after the
  line that is selected (at the end when nothing is), and the whole of it is a length:
  no text, no voice, nothing anchored inside it. It is drawn on the lines lane, hatched,
  because it is *in* that sequence — it sits between two lines the way a line does, and
  everything after it moves back by as much — and it is stretched by **its own right
  edge**, which is the only control it has. The number in the block counts up as you
  drag and the rest of the video follows on release. Only the right edge: moving the left
  one would mean *start the silence earlier*, and that is the length of the line in front
  of it. **Two in a row is refused**, and the button greys out rather than waiting to say
  so: two pauses touching are one pause said twice — there is nothing you could do to
  either that you could not do to one of twice the length — and a track carrying them is
  one where dragging the first looks like it did nothing, because the number you are
  reading is the other one. Drop the line *between* two pauses and they are joined by
  adding their lengths, so the rule is restored where it can be broken and not only
  where it is set. A pause is not one of the things that hold the render back either:
  silence is what it is for, it renders as the seconds it holds, and the voice track the
  preview plays is exactly that much longer. It survives every breakpoint list as a
  read-only row — those lists rebuild the script from what they were shown, so a pause
  with no row would be a pause nobody could keep.
- **Casting is looking at pictures.** Selecting a shot puts the world's whole base in
  front of you as thumbnails, with the number of marked regions on each. Click one and
  it goes on the shot and is **pinned** — a later pass of the matcher may plan around
  your choice but never over it. Click the same one again to re-roll its crop move. Drag
  a picture onto the shot and it becomes a new card in the world's base and is cast on
  the spot, with the markup editor offered while you are still looking at it.
- **What the camera does** is its own block, and it offers two ways to answer, picked
  with one control and **simple by default**. *Simple* is four questions: the KIND —
  hold, push in, drift, zoom in, zoom out, pan — the REGION it converges on out of the
  ones marked on that card, and the two the planner used to roll off a die and keep to
  itself, when the travel starts and how long it runs, read back as *still 1.4 · moving
  1.8 · still 1.4s*. Six named presets cover most shots, which is why they stay the
  default. *Keyframes* is for the rest: a list of moments, each of them WHEN, WHERE to
  look and HOW CLOSE, and the camera runs straight from one to the next and holds
  before the first and after the last. Naming a region on a key puts the window
  **exactly where it was marked** — centre and frame both — and the closeness slider
  then works around that; it used to take the centre and leave the frame at the whole
  picture, and a window that big can only be centred, so the clamp pulled it straight
  back to the middle. Aim at the desk, get the room. The same now holds for a preset
  cast by hand: the quality floor that keeps the automatic matcher off deep crops of
  small cards no longer applies to a shot somebody aimed themselves, because raising
  its window to that floor does not merely soften the move, it re-centres it. Two of those is an ordinary travel — which is
  exactly what a preset is, and why switching to keyframes seeds the list from the
  preset you were looking at rather than starting blank. Three or more is the thing no
  preset can say: hold on the room, come in on the desk, lead away to the door. Each
  moment is also a **small flag on the track**, hanging under the ruler and pointing
  down at the shot it belongs to — drag one along to move it, and pressing either the
  flag or its row in the list lights both and puts the playhead on that instant, since
  the two are one moment shown twice. The
  preset survives underneath a key list untouched, so the choice is reversible in both
  directions and neither costs what you had. Changing the timing does not re-roll the
  rectangles, so the controls can be used together. A move the card cannot make — a pan
  needs two regions marked — leaves the choice where it was rather than inventing one,
  and says so; on a shot with no picture the block is dimmed and says why, since a move
  is built out of a card's geometry. A card that is a CLIP gets the same controls and
  the same move — it used to be refused one on the grounds that a clip has motion of
  its own and two motions over one picture fight, a good default and a bad rule: half
  the clips a world collects are locked-off shots of a room, and `hold` is right there
  for the ones that are not. Clips also get a **poster**, a frame pulled out of them
  and cached, so a card that is a video has a face in the strip instead of a blank tile
  with a badge — picking a picture by its name is the one thing this mode says not to
  do. **▶ play this shot** plays that one shot and stops at its far edge, which is the
  only honest way to judge a move — a still frame cannot show one.
- **Why a move used to go nowhere.** A crop window may not show fewer source pixels
  than the output has, or the picture is being enlarged rather than framed — a fair
  rule with no floor under it, so a card no wider than the video was forbidden to be
  cropped *at all*, and then every push-in, zoom and pan `move_for` could build came
  out with both ends equal and rendered as a frozen frame. Nothing said so anywhere.
  The rule still governs wherever the card is big enough to be governed — a picture
  bought at twice the video is still croppable to half, which is what buys the deep
  move onto a marked region — and it now stops short of forbidding movement outright,
  because a slightly soft move is a worse picture while a frozen one is a broken
  feature. A card small enough for that to bite says **this card is small — cropping it
  softens it a little** in the block, and a track whose moves were decided under the old
  rule gets a **thaw the moves** button, shown only while there is something to thaw: it
  rebuilds every move from the card, kind, region and timing already chosen, so a
  healthy track is rebuilt identically and forty dead shots do not have to be re-cast
  by hand.
- **Preview, and the truth beside it.** The canvas plays the track off one audio file
  built from every line end to end, so the clock does not drift between them: the card,
  its fit, its crop move at that instant, the caption in sync, and a sketch of the
  montage filters. The **filter sliders are right there** and the sketch follows them as
  you drag. It is a sketch on purpose — grain, a tube's misregistered colour and a torn
  scanline are ffmpeg's arithmetic and a canvas can only impersonate them — so **точный
  кадр / the true frame** renders the frame under the playhead through the actual chain,
  at full size, to lay over the sketch and flick between.
- **Nothing here asks a model anything.** Every judgement on this screen is yours, made
  by pointing at a word or a picture, and pressing **build it and go on** marks the
  breakpoint done *without* re-running the stage — re-running it is precisely how an
  hour of hand montage would be handed back to the matcher that was being overruled. It
  refuses while anything would fail to render (a line with no voice, a shot with no
  picture) and says how many of each, rather than letting ffmpeg say it twenty minutes
  later without naming either.

**Settings** opens the run's own settings over the room, because every stage of the
chain is a button on the rail up there and every stage reads them. The room could press
**voice** without being able to choose the voice, and press **metadata** while the
switch deciding whether metadata is written at all sat on a form the run had left an
hour ago. The groups are the stages that can still be pressed:

- **voice** — the engine, the cloned voice, the speech rate, and **I read the lines
  myself** (`tts_source`), which turns the voiceover into the errand hand-made footage
  already is: the stage writes the script out and waits for wav files, and the
  recognizer reads the word timings back off them. One line can be read aloud without
  it — the line's own block has always had that button — and this is for when every
  line will be.
- **the music** — which track out of `assets/music/` plays under the voice, and the
  one control here that is *heard* rather than merely saved: the preview plays it,
  looped under the voice at the level `assemble` will mix it at
  (`[audio] music_volume`). Blank — the default — is not "no music": it is the draw
  the run makes for itself, and the draw is seeded on the run, so the room and the
  finished video land on the same track and the preview is not lying to you. A FOLDER
  (anything ending in `/`) is the same draw over a narrower shelf, which is how a mode
  gets its own kind of music without giving up the draw. `none` is silence. A choice
  that has since been renamed, moved or emptied is not an error either: the run says so
  in the log and rolls again, because a moved file should cost the choice, not the
  music.
- **effects** (`frame_effects`) — whether the model hangs the frame base's prepared
  arrows, circles and sounds on the words itself. What you placed by hand is never
  touched either way; the whole feature is its own section below.
- **subtitles** — the style the `.ass` is written in, and whether swearing is cleaned
  out of the burned-in text while the voice keeps every word.
- **what comes out** — `write_metadata`, the account to publish to, the dry run, and
  whether the temporary files are kept.

They belong to the RUN and not to the video open in front of you — one checkpoint
carries one set of parameters for the whole batch — and they work *forward*: a stage
takes them when you press it, and what is already made stays as it was made, because
saying a line again in a new voice is what re-voicing it means. Each control commits on
its own, like everything else here, and lands on the checkpoint before the sheet
redraws, so a resume afterwards reads what you set. A value that is not on offer — an
engine that is not installed, an account that is gone — is refused with the list of
what is, and the control goes back to what the run actually says rather than leaving a
lie on the screen. What is deliberately NOT in the sheet is the writer's half (the
brief, the world, how far it may invent): **script** throws away every line when
pressed, and a room whose whole point is hand-made lines is the wrong place to make
that inviting.

**I cut it myself** (`frame_by_hand`) is a switch, and it lives in the room, beside the
stages — because it decides what one of them does. With it on, **picture track** never
asks a model which card fits which stretch: it cuts the track out of the speech, leaves
every shot empty, asks for nothing, and hands the casting to you. With it off the same
button asks the matcher first and you argue with what it decided, which is the ordinary
way to use the room. It is on the wizard's form too, where it also adds the `picture`
breakpoint to an ordinary run — one that walked past it would carry an empty track into
the footage stage — but a setting that changes what a button in here means belongs where
the button is.

## The effects base (`configs/effects/`)

A frame base says **what is on screen**. The effects base says **what goes off on top
of it**: an arrow pointing at the thing being named, a ring drawn around it, a sting
under a line. It is the one layer of this mode that is deliberately *synchronous* with
the narration — the picture track runs past the speech on purpose (that asynchrony is
most of what makes a wall of stills read as edited footage), and an arrow that arrives
half a second after the word it points at is not a late arrow, it is a wrong one. So an
effect is placed on a **word**, exactly as a cut is, and survives re-voicing for the
same reason: the seconds move, the word does not.

- **One base for every world**, unlike the frames, and that follows from what the two
  things are. A card is a picture *of somewhere* and belongs to the world it is of; an
  arrow is an arrow anywhere, and a base that had to be copied into each new world
  would be copied wrong. What binds an effect to a world at all is the card that hangs
  it on one of its own regions — and that lives on the card, where the coordinates are.
- **An effect is a picture, a clip, a sound, or a picture with a sound.** `configs/
  effects/<name>.toml` with its material beside it, the way a card lives beside its
  picture and a cloned voice beside its sample. A sound alone is a whole effect and
  draws nothing; a picture alone is a silent one. Drop a file into **Configuration →
  effects** and it becomes one; the second half is brought in the editor.
- **`description` is what makes it reachable by a model.** It is one line saying *when
  this effect is the right one* — "когда в тексте называют предмет, который видно на
  картинке" — and it is what the effects pass reads. An effect nobody described is
  perfectly usable by hand and will simply never be chosen automatically, which is the
  honest outcome: a model cannot guess what an unnamed png means.
- **Three anchors, and the first one travels with the picture.** `point` places the
  effect on the picture itself — a point dropped anywhere on the card, or a box dragged
  around something — and the crop move is a window over that same picture, so a ring
  around a cap converges with the zoom exactly as the cap does. The path across the
  frame is computed from the move as arithmetic and handed to ffmpeg as a path, rather
  than being drawn in screen coordinates and left to drift off the thing it points at.
  `screen` puts it at one of nine places in the frame whatever is behind it: a flash, a
  border, a sting, anything with no picture at all. `full` covers the whole frame — a
  light leak, a tape glitch, material that is not *in* the picture but *over* it, so it
  is scaled to cover rather than placed. That difference also decides *where an effect
  can come from*: a point effect is coordinates on one file and can only be offered by
  that file's card, while the other two ask nothing of what is on screen and are offered
  over every stretch.
- **A point, and not a marked region.** A crop target exists to be *looked at*: it is a
  window the camera can move into, so it is a box of a particular size around a
  particular thing. Requiring one to put an arrow somewhere meant the base could only
  point at things somebody had already decided to zoom into — and half of what a video
  wants to point at is not a thing at all: a corner, a gap in a queue, the empty half of
  a desk. So the placement answers to nothing. Drop a point anywhere, or drag a box
  around whatever you meant, and drag it again when it is wrong.
- **`width` is a fraction of the frame** — 0.35 is a third of the screen — and it sizes
  the effect wherever nothing else does: a screen effect, or one dropped on a single
  point. Give it **two** points and they are the size as well as the place, and `width`
  says nothing: the box you dragged *is* the box, because a multiplier on top of a
  direct manipulation is a second control for one decision. Under `full` it says nothing
  either.
- **The animation is a run of moments**, the same shape a hand-placed crop move has:
  when, how big, how far off centre (in the effect's own widths AND its own axes, so an
  arrow's tip stays on its target however far the camera comes in and whichever way it
  has been aimed — pointed left, it comes in from the right), how turned, how solid. Everything between two
  moments is interpolated; before the first and after the last it holds. Five ready-made
  ones fill the list in one press (**rise**, **pop**, **blink**, **float away**, and
  none at all) and are a starting point rather than a type — what is saved is the
  moments, so wanting the pop a tenth slower is an edited number and not a feature
  request.
- **The editor is a stage and a strip**, because those are the two things you are
  actually doing: moving the thing, and deciding when it does that. The stage is the
  video's own shape with the effect on it — **drag it** and you are editing the selected
  moment's offset, drag its corner and you are editing that moment's size, drag the knob
  above it and you are aiming the whole thing — and a point effect is
  shown over a stand-in square, which is what its size is measured against on a real
  card. Under it, every moment as a marker on the effect's own clock: press the strip to
  move the playhead, drag a marker to move that moment in time, **＋ a moment** to add
  one where the playhead is (it takes the values the animation already has there, so
  adding one never changes what the effect does — it only gives you somewhere to change
  it). The numbers stay, for when a number is what you have; they are simply no longer
  the only way in, which is what made the first version of this screen a spreadsheet
  about a picture nobody could see.
- **Turned, too.** Every moment carries a `rotate` in degrees, so one picture of an
  arrow points in every direction there is and a base does not need eight of them. It
  animates like everything else: an arrow may swing in, a stamp may land crooked. On
  the stage it has a knob of its own above the thing — drag it round, hold **Shift**
  for fifteens — and it turns about the centre, which is the only pivot that survives
  being resized. In the render it is the `rotate` filter with the angle as an
  expression; the canvas it turns on grows to the picture's diagonal so the corners are
  not cut off, and the scale that follows is given that padding back, which is why a
  turned effect is the size you asked for rather than that size divided by √2.

- **Turned, and turned over time.** A moment carries an angle like it carries a size,
  so one picture of an arrow points in every direction there is and a base does not need
  eight arrows. Two moments with different angles is a swing — an arrow that arrives
  turning, a stamp that lands crooked. ffmpeg spells its clock `t` in `rotate` and `T`
  in `geq`, and the render gives the rotation its padding back so a turned picture is
  the size you asked for rather than the size of its own diagonal.
- **Aimed where it is used, in three places that add up.** The ANIMATION turns
  (`EffectKey.rotate`, the knob on the effect's own stage); the CARD aims — which way
  the arrow points on *that* picture, the knob on its ghost in the card editor or the
  **turn°** field beside it; and a single FIRING aims again — which way it points over
  *this* line, **Shift**-drag on the montage preview or the **turn°** field in its
  inspector. They ADD rather than override, which is the point of having three: an
  arrow aimed at a doorway on one card still swings in the way its animation says, and
  nudging it for one video does not throw away the aim every other video is using.
  **Shift** snaps to fifteens everywhere, because most of what anybody aims at is a
  right angle or a diagonal. The knob in the effect's own editor turns **every moment**
  by the same amount, and that is not a shortcut — it is the difference between aiming
  a thing and animating it. Turning one moment and leaving the rest at zero does not
  point the arrow: it makes it spin up to the angle and back down, which reads as the
  animation having broken. A swing is authored deliberately, with **Alt** (this moment
  only) or by typing into that moment's **turn°**.
- **Two doors, because there are two different acts.** An effect the CARD carries —
  aimed at something on that picture, the same list the automatic pass fires from — is
  fired from the picture: select the shot and press **＋ this picture's effect**, and it
  goes on the word under the playhead. Anything else you own is fired from the lane
  under the shots: press it above a word and the rest of the base is offered, to be
  dropped in and dragged where you meant. Each menu says in a line where the other one
  is, so nothing is hidden; each row shows the thing itself and what pressing it will do
  ("goes to its own point on the picture", "lands in the middle of the frame — drag it
  from there").
- **And the timeline draws that difference instead of explaining it.** A shot's block
  **stretches downward** and its own firings live inside it: they are things that
  picture does, they are clipped to it, and a cut or a re-voicing that shortens the shot
  shortens them. Everything laid over the frame by hand sits on its own lane below,
  outside any block. Where two of the same kind overlap in time the later one drops to
  the next row and the lane (or the block) grows by a row, so the one that starts
  earliest is always the one on top and nothing is ever drawn over anything.
- **Moved in TIME, freely — and that is the difference from a cut.** Take a firing's
  block on the lane (or inside the picture it belongs to) and pull it along the track. It
  goes wherever you drop it: a cut is a word because the picture changes on a syllable or
  it changes wrong, while an accent is aimed at the speech rather than fastened to it — it
  leads a word by a fifth of a second, it covers the breath between two, it runs across
  three — and snapping it to word starts would put half the placements you can see out of
  reach. What is *stored* is still an anchor, which is the part that is not obvious: the
  second you dropped it on is written down as the nearest word plus the remainder, so a
  later re-voicing carries the firing with the speech it was aimed at instead of leaving
  it on a second that now belongs to another line. Dragging pins it, like anything else
  placed by hand. A firing the PICTURE carries stops at the edges of its own still — its
  coordinates are a fact about that one card and the render ends it where the shot changes,
  so a drag past the cut would not move it, it would delete it — while one you dropped in
  from the base is re-hung on whatever picture it lands on, and its own placement goes
  with the old card, because those coordinates were measured on a picture that is no
  longer under it.
- **Moved in SPACE for one video, on the preview itself.** Select a firing in the montage
  room and the preview takes the pointer: drag it and it goes where you drop it — worked back
  through the crop window that is up at that instant, so what is stored is a point on
  the CARD and the effect goes on travelling with the picture. It is stored on the
  **cue**, not on the card, so the arrow keeps pointing where it always did in every
  other video, and **put it back where the card says** undoes it. A box keeps its shape
  while it moves; an effect that was never placed becomes a point where you dropped it.
- **The true frame draws them too.** The `точный кадр` button renders one frame through
  real ffmpeg, and it is the button that answers "what will actually be there" — so it
  stamps whatever is going off at that moment, at the size, opacity and angle the
  animation has arrived at, in the same place in the chain the delivery pass puts it.

- **A middle that repeats: in, loop, out.** An animation's clock can be cut by **two
  separators** into the way *in*, the part that *repeats*, and the way *out* — and then
  a firing is the entrance, the middle however many times, and the exit. Both are
  dragged on the strip, which is also where the moments live, because a key at 0.4s
  belongs to the entrance or to the loop depending on where the separator sits and
  asking you to keep two numbers in agreement about that is asking you to do arithmetic
  the editor can simply show. The same three parts are there as lengths (**way in**,
  **middle**, **way out**) for when a number is what you have, and **repeats** is the
  default count.
  The **length is never typed** for one of these: three pulses *are* three pulses long,
  so the count is the control and the seconds are the readout. A cue on the timeline
  may say a different count — three pulses here, six over the next line — and the
  montage room's inspector shows **repeats** instead of a duration for exactly those,
  with the arithmetic beside it (`× 0.65s = 1.95s`). Nothing about the render changes:
  the repeat is a fold in the animation's clock, so the sampled path comes out the same
  shape it always did and ffmpeg is told nothing new.

- **A clip either loops or ends the effect.** `hold` runs it for its hold and loops
  whatever is shorter — a sparkle, a pulsing ring, material with no ending of its own.
  `clip` makes the clip's own end the effect's end: an explosion that resolves, a stamp
  that lands. `.webm` is worth knowing about here: it is the one format that carries an
  alpha channel through a video codec, and an arrow with square black corners is not an
  arrow.
- **A card hangs one on the picture, by dragging it there.** In the card editor, under
  the regions, is the list of what this picture can *do*: which effect, one line saying
  what firing it means **here** — «обвести шапку» — and three placements: a **point**, a
  **box**, or **in the frame** (not on the picture at all). Press one and the effect
  appears on the stage, drawn the way it will actually look, with a dot marking what it
  is pinned to; drag it onto the thing, drag its corner to resize, drag the knob above it to aim it. That line is what the
  model picks on, so "эффект 3" is not enough and «обвести шапку» is. The ghost is drawn
  at the moment the animation has ARRIVED at — offsets and all — rather than at its
  anchor: an arrow that hangs above what it points at would otherwise be drawn sitting
  on it, and every arrow in the base would get placed one arrow-length too high by
  somebody doing exactly what the picture told them.
- **Who fires them.** After the pictures are chosen — and only then, because only then
  is it settled which card is up over which stretch and therefore what that stretch can
  do — one model call is made over the whole video. It is given each stretch, the
  picture on it, what that picture has ready, and the words being spoken numbered end to
  end; it answers with a word number per effect. What it may **not** decide is rhythm:
  no two effects closer than two seconds, the same one not twice inside twelve, and a
  ceiling of eight a minute over the whole track. Meaning and rhythm are different kinds
  of judgement and neither is any good at the other's job — the same split the frame
  base makes between its matcher and its picker.
- **It costs nothing where nothing is prepared.** A base with no effects, a world whose
  cards hang none, or the switch off, and the call is never made. **Let the model place
  effects** (`frame_effects`) is on the fandom form and in the montage room's settings;
  off, it stops the *choosing* only — what you placed by hand is never touched by it,
  and never dropped by the rhythm rules either.
- **In the montage room** the effects have a lane of their own, between the stills and
  the words, which is where they are in the video too — drawn on the picture, placed on
  a word. Press the empty lane above a word and you are offered what that picture can
  fire there. Press a block to select it, set how long it stays up, or take it off. The
  preview draws them over the frame, from the same path the render uses rather than a
  second implementation of the one piece of arithmetic here that is genuinely hard. An
  effect whose file has since left the base is still drawn on the lane, marked, and
  still yours to remove; one whose card never had it placed anywhere says so, because
  it will hang in the frame rather than on the thing.
- **Nudged for one video.** An effect is placed once, on the card, and that is where
  it goes in every video that fires it. Sometimes one line wants it half a hand to the
  left — so a firing selected in the montage room can simply be **dragged on the
  preview**. The drop point is worked back through the crop window that was up at that
  instant, so what gets written down is a place on the CARD, and the effect goes on
  travelling with the picture exactly as before; it is written onto the CUE, so the
  card keeps saying what it says everywhere else. The inspector says when a firing has
  been moved and offers it back (**put it back where the card says**). A box keeps its
  shape while it is dragged: every point moves by the same amount, so the ring around
  something does not resize itself on the way.
- **The true frame draws them.** «Точный кадр» renders one frame through the real
  chain, and it now stamps whatever is going off at that moment onto it — at that
  instant the ramps collapse to numbers, so the effect is a plain overlay. A button
  that exists to answer *what will actually be there* may not leave out the layer you
  just placed.

- **Where they land in the render.** In the delivery pass, after the montage filters and
  before the subtitles: an arrow is a graphic meant to be read, so it stays out of the
  grain, and the captions stay over everything because they are what the video is
  saying. Their sounds are mixed in the same pass, delayed to the moment they go off, at
  their own volume and without renormalising the voice — so the narration does not get
  quieter the more stings a video happens to carry.

## Visuals profiles (`configs/visuals/`)

The video track is a layered composition, configured per profile:

- **Background**: `stock_video` / `stock_photo` / `local_video` / `local_photo` / `ai_photo` / `ai_video` (free keyless generation — Pollinations images, Wan video via HF Spaces). Linkage `narration` = the LLM emits a photo/footage query for every ~N seconds of speech, tied to what is being said at that moment (Switzerland → the capital, a couple → a couple, a puppy → a puppy); `neutral` = random/looping content (e.g. gameplay). Photo backgrounds get Ken Burns motion (`none`/`subtle`/`strong`) and change every `interval_s`.
- **Foreground**: optional framed picture/clip inserts that are *event-driven*, not on a timer — the LLM decides which spoken phrases deserve an illustration, and each insert appears exactly while that phrase is spoken (timed from edge-tts word timings) and disappears afterwards. You only pick the source, width and position.
- **Who supplies the material** (`manual = true`, on either layer): a flag, not a source of its own — *where the material comes from* and *what kind of material it is* are two independent questions, and you can step into either family. `stock_video`/`stock_photo` + `manual` is a **search**: slopgen says what each shot needs and hands you the words to find it with, you find the file. `ai_video`/`ai_photo` + `manual` is a **generation**: slopgen writes the prompt, you make the clip in an external web tool. `local_*` ignores the flag — those files are already on disk. `ai_model` is ignored wherever `manual` is set, because there is no generator left to name; this pair of settings replaces the old `ai_model = "manual"` spelling, which could only ever express the generation half.

Shipped profiles: `classic` (stock video b-roll, the default), `slideshow` (narration-synced Ken Burns photos), `gameplay` (drop your minecraft-parkour/subway-surfers clips into `assets/footage/gameplay/`, narration photo inserts pop in front), `ai_slideshow` (keyless Pollinations images synced to the narration), `ai_broll` (a generated clip per scene — free, slow), `ai_manual` (you generate every clip by hand) and `search` (you find every shot yourself). **Both doors take the profile apart.** The TUI wizard's Visuals step and the browser's
Picture card hold the same controls — background source, who supplies it, the generator,
the linkage, the folder, the photo cadence and motion, one-clip-straight-through, and the
inserts' own source, width and position — and both fill them FROM the profile you picked.
That is what lets a card with fourteen controls on it change nothing: a form nobody edited
rebuilds the profile exactly, so the run carries the profile's NAME, which is what the
runs list shows and what stays correct when the profile is edited later. Move one field and
the run carries its own copy instead. A field the card does not ask about keeps the
profile's answer rather than falling back to the model default, and `manual` blanks the
generator pick, because a source the operator supplies has no generator to name — which is
why picking `ai_manual` and touching nothing is not an edit.

### Art style: one description, every prompt

A field on the **Visuals** step of all three wizards (`--visual-style`), where you say how the video should LOOK in your own words — «аниме», or three paragraphs about grainy 16mm and sodium street light. It is compiled **once per run** into the dialect image and video models actually answer to — a short comma-separated English tag descriptor, capped at 16 tags — and then appended to **every generated prompt of the run**: background and foreground, clips and stills, info clip and drama and fandom alike, the prompts slopgen sends itself and the prompts you paste into Kling by hand. It binds the LOOK alone, exactly as `--visual-notes` binds what may be shown: not a word about what is in the frame, because it rides on prompts that already say that, and one stray noun would put that noun in all forty shots.

It is only ever *asked* where it can be answered: the field appears when something in the run is actually generated — an `ai_photo`/`ai_video` background or insert, or a generator chain (`manual` counts, since a hand-made shot is made from the same prompt) — and stays hidden for stock, local footage and a pure search, which have no prompt to put a look in. Typing one and then switching the source back does not smuggle it into the run either: what the wizard no longer shows, it no longer asks for.

Both ends of what people write need that compile. «Аниме» pasted in raw reaches the generator as one foreign token, which it renders as a caption across the frame; even written "anime" it is a word rather than a look — what pins the look is the handful of tags the word stands for (cel shading, flat colour, thick clean linework, painted backgrounds). A long description has the opposite problem: it is sentences, it mixes in facts about the story, and appended to forty shot prompts it out-weighs the shots themselves. So the short one is expanded into what it means, the long one boiled down to what it shows, and both come out the same shape and size.

It costs one LLM call the first time a look is used and nothing after: the result is cached on disk keyed by the text you wrote, so resumes, later episodes and later runs reuse it for free. A failed call never costs the run — your own words are used when you wrote them in English, and nothing at all when you did not, since a shot without style tags is still a shot while one captioned in Cyrillic is ruined. On a **search** the tags are never typed into the queries (a stock index knows what a picture is *of*, not how it was drawn): the look reaches the briefer as a preference instead, honoured where real material could plausibly have it (black-and-white, archival, neon night, shot on film) and ignored where it could not.

### Filters: one look over the whole cut

The other half of the same wish, at the other end of the pipeline. A block on the **Visuals** step of all three wizards (`--filter NAME[=DOSE]`, repeatable) that lays effects over the **finished, cut video** in ffmpeg: `bw`, `film`, `bloom`, `vignette`, `grain`, `vhs`, `crt`, `glitch`.

Where an art style is *asked of a generator* and answered differently every time — and cannot be asked at all of a stock clip or a folder of your own footage — a filter is applied to the frames that came back. So it works in every mode, from every source, and looks the same whatever made the picture. Nothing about it depends on there being a prompt anywhere in the run.

Each effect is a **dose from 0 to 100**, not a switch: 20 is a suggestion, 100 is the joke. They stack, and they always stack in the order they are listed — grade (`bw`, `film`), optics (`bloom`, `vignette`), medium (`grain`, `vhs`, `crt`), signal (`glitch`) — never in the order you switched them on, because a grain added before a blur is a blurred grain, which is not what anybody meant by either.

It covers **the whole video, end to end**, and it is deliberately not a per-shot thing: half of these effects are a story about what the video is supposedly playing on — a tape, a tube, a projector — and a tube that shows up for one shot and leaves is not that story, it is a transition. A drama cut into episodes gets the same look on every episode for its full length.

The effects run on the **picture only**. Subtitles are burned in and the ad overlay is stamped *after* them, and stay clean: both are meant to be read, the platform reads them too, and hash over a caption costs legibility for nothing. All of it happens inside the delivery pass that was already re-encoding the video, so the whole thing costs a fraction of one pass and no extra generation, no extra API call and no extra file.

### User-assisted material: you generate it, or you find it

Both `manual` kinds park the run at the footage stage in a clean `paused` state (not a failure), write the shot list out, and wait for you — `slopgen gather`, or the Gather screen in the TUI, or just files dropped into the run's `manual/inbox/` named `shot_NN.<ext>`. Generation is the older of the two and the simpler to describe: one prompt per shot, pasted into Kling/Veo/Pika, the file handed back — you beat the free daily limits by hopping services and accounts, which is exactly what a machine cannot do for you.

**Search** is the same shape around a different problem. slopgen cannot look at a clip and tell whether it is usable, so it does not pretend to search a stock library the way a person does; it does the half it is genuinely good at instead — deciding what each shot needs and writing the words most likely to surface it. That is a translation job rather than a copy, because a shot prompt and a stock index are different languages. "Марта bends over the sorting table, lamplight catching the wax seals, slow push-in" is nothing any library holds; what it holds is "woman sorting mail warm light" and "hands wax seal close up". So one LLM pass rewrites every shot twice over: a one-sentence **brief** in the narration's own language, written for the person doing the looking (framing, mood, and what must *not* be in the frame — "no faces", "no text", "shot from above"), and **3-5 short English queries** for the sites, which index English whatever the video is spoken in. The queries come ordered most promising first and deliberately varied — a literal one, a broader one, a mood one — so that a dead first query is not a dead shot.

The same pass decides **photo or video, per shot**, because that choice belongs to the material rather than to a setting: a wax seal in close-up wants a photograph, mules on a path want motion, and someone sent after *video* of the former comes back with nothing usable. It is advice, not a rule — whatever you actually deliver is what counts, the manifest reads the kind straight off the file you hand in, and a still is simply held and panned (Ken Burns) to the length the beat needs.

Everything downstream is the machinery user-assisted generation already had, reused rather than rebuilt: the same `manual/manual_shots.json` manifest (authoritative on disk; the Gather screen is a view over it), the same `manual/prompts/<shot-id>.txt` mirrors — where a search task holds the brief, a `[photo, ~7.0s]` line and the queries under it — the same inbox, which now accepts images (`.jpg` `.jpeg` `.png` `.webp`) as well as video, the same `slopgen gather`, and the same per-episode rules for a drama. Briefing is **batched at 20 shots per request**, since a hundred-odd shots in one response come back with the tail written as "similar to the above"; a batch whose call fails degrades to the plain shot description rather than taking the run down — a worse search, but still a search.

## LLM profiles (`configs/llm/`)

Named connections: `provider` (`deepseek`/`gemini`/`openrouter`/`custom`), `model`, `base_url`, `temperature`, `web_search`. The active one is chosen by `[llm].profile` in `slopgen.toml`. API keys never live in TOML — they are env variables in `.env`; the TUI Configuration → LLM section lets you pick model presets per provider, paste the key (saved to `.env` automatically), toggle web search, activate and delete profiles.

**`temperature` is set for the prose, and the JSON is re-asked cooler.** Every stage but
one talks to the model in JSON, and above roughly 1.2 a model stops being able to hold
the envelope its prose travels in — it degenerates mid-sentence and then writes past the
closing quote, so a perfectly good idea comes back unparseable. Measured on
`deepseek-chat` at `temperature = 2.0`: 3 in 10 single answers were invalid JSON with a
short niche brief and 8 in 10 with a long one — which, retried three times, is one failed
stage in thirty-seven for the short brief and one in two for the long one, the second
being frequent enough that it reads as "topic generation is broken". So the first try
uses the temperature you set and a try that comes back unparseable is re-asked at a
ceiling of 1.2, then 0.7. It is only ever a ceiling: a profile at 0.7 is untouched, and
heat is taken away only from a model that has just proved it cannot spell JSON at that
heat, so the answer that reaches the video is the hottest one that actually parsed. Set
the temperature as high as you like — the deliberately stupid profile is a real use — and
know that the duds you get there are the model, not a crash.

**Web search** (`web_search = true`): gives the model a real `web_search` tool via standard OpenAI function calling. Before writing the script the model calls it, slopgen runs a keyless DuckDuckGo search and feeds the results back, so the narration is grounded in real, verified facts instead of invented names/events. Works on any provider whose model supports tool use (OpenAI, DeepSeek, OpenRouter, Gemini's compat endpoint); a model without tool calling will simply not use it.

**Prices and the bill.** `price_in`, `price_cached` and `price_out` are what the model costs in USD per million tokens, taken off the provider's price list (cached input is what a prompt-cache hit costs; leave it 0 and a hit is billed like a miss). They are configuration rather than a table shipped with slopgen because prices change and a stale one quietly reports the wrong number. Left at 0, a run still counts every token — it just cannot put money on them. Either way the whole bill goes into the run's `checkpoint.json` as it happens and reads back with:

```bash
slopgen usage output/20260827_213735_fandom_ru        # tokens per stage, retries, cache hits, cost
slopgen usage output/20260827_213735_fandom_ru --calls  # every single call
```

`RETRY` is the column to look at first — a retry pays for the whole context again — and `CACHED` says how much of the input the provider served out of its own prompt cache instead of reading.

**One model per job (`[llm.stage_profiles]`).** The calls slopgen makes are not alike. Writing a script off a whole world is what an expensive model is for; compiling one character's appearance into image tags, naming a finished video, turning a shot into a stock-footage query or rewriting a line to swear more is errand work, and on a fandom run the errands outnumber the writing. Every call already carries a kind, so routing is one table in `slopgen.toml`:

```toml
[llm]
profile = "deepseek"          # everything not listed below

[llm.stage_profiles]
char_compile  = "cheap"       # one character's looks -> image tags
style_compile = "cheap"       # the run's art style -> English tags
metadata      = "cheap"       # title/description/tags of a finished video
lookup        = "cheap"       # shot -> stock-footage search queries
idea          = "cheap"       # pick a topic for an info clip
```

Kinds: `idea`, `length`, `script`, `drama_outline`, `drama_script`, `fandom_outline`, `fandom_script`, `fandom_canon`, `fandom_brief`, `lore_lookup`, `drama_entities`, `drama_shot_fix`, `char_compile`, `char_autofill`, `style_compile`, `metadata`, `profanity`, `censor`, `lookup`, `bp_rewrite`, `bp_scenes`, `vision`. An unlisted kind, a profile that does not exist and a profile whose API key is missing all fall back to the active profile — a wrongly-configured cheap model never costs you a script.

Stock-footage API keys (Pexels, Pixabay) can also be pasted in the TUI under **Configuration → Footage API keys** — they are saved to `.env`. They're only needed for `stock_*` visuals; local assets need none.

## Voice engines and cloning (`[tts]`, `configs/voices/`, `slopgen models`)

Four engines, picked by `[tts].engine` (or `--tts-engine`, or TUI → Configuration → Voice engine):

| engine | Russian | word timings | speed | cost |
| --- | --- | --- | --- | --- |
| `edge` **(default)** | 2 voices + 12 multilingual | **free with the audio** | fast | free, no key |
| `azure` | the whole 700+ catalogue, incl. Dragon HD Omni | **free with the audio** | fast | ~$22 / 1M chars |
| `qwen` | yes, and clones | needs the aligner | fast | ~$13 / 1M chars, 1M free for 90 days |
| `qwen-local` | clones only | needs the aligner | RTF ~5 on CPU | free, 2.3 GiB of weights |

Word timings are what the subtitles and the drama montage are built on. Edge and Azure report them themselves; the two Qwen engines do not, so a small recognizer reads them back off the finished audio — install it first: `slopgen models install vosk-ru-small`. It is not transcribing anything (the words are already known, it is only asked *when* each one was said), which is why 46 MiB is enough; measured against edge-tts' own timings it lands within ~18 ms on average.

**Models are not in the repo.** `slopgen models list | install <id> | remove <id> | path` downloads them into `paths.models` (gitignored), with the exact size and licence printed before anything starts, and pip dependencies installed as a separate, explicit step. An interrupted install resumes from the byte it stopped at. Same thing on the **Models** screen in the TUI.

**Cloned voices** live in `configs/voices/` as a card plus the audio sample next to it:

```toml
# configs/voices/марта.toml
name = "марта"
lang = "ru"
default = "обычная"                # which delivery `--voice марта` speaks with

[samples.обычная]                  # every recording sits on one level…
ref  = "марта.обычная.wav"         # the file, next to this card
text = "…exactly what is said in it, typed out…"

[samples.зло]                      # …and this is the same person shouting
ref  = "марта.зло.wav"
text = "…exactly what is said in THAT one…"
description = "shouting, pressing on every word"
```

```bash
slopgen voices add sample.m4a --name марта --text "…" --clean   # checks + converts + writes the card
slopgen voices check sample.m4a --text "…"                       # just measure it
slopgen voices check марта                                       # …or an existing card, transcript and all
slopgen voices record марта angry.wav --as зло --text "…"        # another delivery of the same person
slopgen voices default марта зло                                 # …and now the card shouts by default
slopgen voices rename марта:зло ярость                           # a delivery…
slopgen voices rename марта Марта                                # …or the whole card
slopgen drama ru --tts-engine qwen-local --voice марта            # whatever марта's default is
slopgen drama ru --tts-engine qwen-local --voice марта:зло        # …that take, whatever the default is
```

There is no training step — cloning is zero-shot, so the card **is** the voice and works on any engine that clones. **What a usable sample is: one continuous take of one person, transcribed by hand from that very recording.** Not a montage, not a scene with music under it, not a clip with pauses in it — the model is handed the sample and the transcript together with every line, and if it cannot find the transcript in the audio it finishes the transcript out loud, in the middle of your script. Measured on a 33-second montage with 60% silence in it and 5 of its 51 transcript words findable: three takes out of three of a five-word line came back saying the sample instead. A cloning run therefore listens to its own reference before it voices anything, and when the sample scores badly it voices one short line to find out whether that matters — refusing only if the model will not say it. The score alone never refuses: a recognizer that cannot make out a child, a whisper or a heavily processed voice scores a perfectly good sample badly, and a suspicion is not grounds to reject somebody's recording (`[tts] check_reference = false` turns the whole gate off); `slopgen voices check <name>` asks the same question of a sample you are considering. Note what is *not* on that list: length. Five clean references of the same voice at 6.1s, 12.2s, 24.1s, 43.4s and 61.3s produced thirty takes that are indistinguishable (median score 0.81–0.82, not one leaked word at any length) — what a long sample costs is time, about 0.4s of extra synthesis per second of reference on every line. Two more things are worth knowing. The transcript is typed by hand on purpose: a recognizer's errors do not stay put, and a wrong transcript has been measured making the model speak words *from the sample* in the middle of a line. And the sample is checked when you add it, not when you render: a clipped or hissy reference fails silently, degrading every line of every video made with it, so `voices add` refuses the samples that are known to break cloning and offers `--clean` (RNNoise via ffmpeg — `slopgen models install rnnoise-sh`) for the ones that are merely noisy.

**Intonation is another recording, not a parameter.** No cloning engine here has a knob for anger or for a whisper: the model imitates the reading of the sample it was shown, so the way to make a voice shout is a take of that person shouting. A card is therefore a **catalogue** of takes of one person — `[samples.<name>]`, all on one level, none of them a lesser thing than the first one you cut — and `default` names the one the whole video speaks with. They are addressed in the same namespace one level down: `марта:зло` is that delivery, a bare `марта` is whichever delivery the card currently points at, and both go wherever a voice name goes (`--voice`, the content type's `voices`, the demo, a single line at the voiceover breakpoint).

The difference between those two spellings is the whole design. `марта:зло` is pinned to a recording and stays there. `марта` is an instruction — "whatever this card's default is" — so moving the pointer (`slopgen voices default марта зло`, or the ★ button in the browser's voices room) changes what every run and every unpinned line comes out as, and moving it back changes them back. It re-voices rather than merely re-labelling: the recording is part of the voiced-line cache key, so the lines that were following the default are synthesized again in the new delivery while the lines pinned by name are left exactly as they were. A line pinned at the montage screen (`Scene.voice`) is the same mechanism per line: re-pinning one line re-voices that line and leaves the other forty alone — exactly as the per-line speed already does.

**Renaming.** The name is a field on the card and on each delivery in the browser's voices room — Save commits it — and `slopgen voices rename <card|card:delivery> <new>` does the same from the terminal. It takes along everything that is a SETTING: the card's file, the recordings named after it (a wav another card also names is left where it is), the pointer at the default delivery, and every content type that named this voice. It does not take along a pin inside a run — `Scene.voice` in a checkpoint, a `--voice` already typed into a command — because those record a particular video rather than a setting. A run resumed after a rename fails at the voicing stage, by name, rather than quietly speaking forty lines in the wrong delivery. Rename before you pin, not after.

**And the casting can be the writer's.** `--deliveries` (a switch on the start form, and in the montage room's settings) adds one LLM call before anything is voiced: the finished script goes out with the card's deliveries and their descriptions as the only vocabulary, and what comes back is the exceptions — the lines that are shouted or whispered, pinned to the recording that is. Everything else keeps the empty spec and therefore the card's default, so moving the default afterwards still moves those lines. Three things are the code's and not the model's: a delivery name nobody recorded is dropped rather than resolved, the share of lines it may recast is capped at a third (a video that switches every third line does not sound expressive, it sounds like it changed narrator), and a pin you made **by hand** is sent as context and never overwritten — `Scene.voice_auto` is what tells them apart, and the montage room marks the writer's with `∿`. An automatic pin on a line whose text you have since rewritten is taken back on the next entry to the stage. Describe the takes when you cut them (`description` on each `[samples.<name>]`): `зло` and `тише` mean whatever you meant by them, and that sentence is the whole of what the model knows about the recording.

Two things are worth knowing before cutting the takes. **Cut them out of ONE session**, ideally one continuous recording: timbre travels with the delivery, and a sample recorded closer to the microphone or on another day clones as a slightly different person, so a video switching between two of those changes narrator mid-sentence. And what this buys is per-LINE control, not per-word — a whole line comes out in the delivery of the sample it was voiced with; leaning on one word inside a line is an SSML matter, and SSML here belongs to Azure.

**No engine at all** is also an option. `--tts-source manual` writes the script out and waits for you to drop `scene_NN.wav` into the run's `manual_voice/inbox/`, exactly as `--manual` does for footage — for the good voices that have no API, and for reading the lines yourself. Timings come from the recognizer.

## Configs (`configs/`)

Everything is hand-editable TOML; a new file in the folder = a new entity, no code changes.

- `slopgen.toml` — global: video size/fps, target duration, subtitle style/font/colors, music volume, active LLM profile, footage provider order, UI language/theme, defaults, and `[tts.pronounce.<lang>]` (below).
- `content/*.toml` — content types: per-language creative briefs (`idea_brief`, `script_brief`), `voices` per language (a catalogue id for the active engine, or the name of a `configs/voices/` clone), `fallback_keywords` for stock search.
- `ads/*.toml` — ad contracts: `url`, overlay section (assets dir, caption `text`, `position`, `start_s`, `duration_s`, `width`), native section (assets dir, `talking_points` the LLM weaves into the script), description `snippet` (`{url}` is substituted). `modes` says which of the two halves may run, and the pipeline wants both it and the section before it runs one. The browser edits the same file under **Configuration → Ad contracts**, and there the two are one tick each: the tick writes the section and the mode together, so they cannot disagree. The banner is drawn in a frame at its real width and in its real corner, `{url}` is substituted under the description as you type, and each assets folder is counted where it is named — an empty one fails four stages into a run otherwise. A contract can also be **typed into a start form instead of picked**: the contract dropdown's last entry is «— type one in —», which opens the link, the corner caption and its position and timing, and the talking points right there. It is saved nowhere — it belongs to that one run, the way a custom visuals profile does — and its material goes in `assets/ads/manual/overlay` and `.../native`, which are created for you when the run starts. Both halves are always written and `--ad-mode` decides which one the video spends, so switching overlay↔native never means retyping the contract. Every mode's form offers it, since an ad is the one thing about a video that has nothing to do with which mode made it.
- `accounts/*.toml` — publishing targets: `platform`, YouTube OAuth paths/privacy/category, optional `defaults` (lang/type/ad).
- `presets/*.toml` — full parameter bundles for one-command runs.
- `characters/*.toml` — AI-drama cast members (`name`, `age`, `appearance`, compiled `visual_prompt`).
- `fandoms/<name>/` — a **folder**, not a file, because a world is more than settings: `fandom.toml` (`docs` in reading order, `tone`, `lore_tool`, plus the machine-written `canon` + `docs_sha`), one or more `.md` lore documents, and `characters/*.toml` — the world's own cast, shelved with the world it belongs to and written differently from the global `characters/`: `appearance` plus `plurality` (`one` / `many` / `class`), no age and no personality, because a world's character is a look and the rest of it is lore. The folder name is the fandom's identity; the TOML is optional.
- `effects/*.toml` + the picture, clip or sound next to each one — the effects base: what a card can DO while it is up (`anchor` point/screen/full, `place`, `width`, `hold`, `fill`, `volume`, `keys` — each with its own `rotate` — the animation as moments — and `loop_from`/`loop_to`/`loops`, the two separators and the repeat count). One base for every world; which card fires which is written on the card. See [the effects base](#the-effects-base-configseffects).
- `orchestration/*.toml` — AI-drama generator chains (ordered `[[stages]]` with `model`/`key_mode`/`key`/`metric`/`amount`, plus an optional per-stage `clip_seconds`); a stage's `model` may also be `manual` or `search`, which are the operator rather than a generator.
- `visuals/*.toml` — visuals profiles: background source/linkage/AI model/interval/motion/continuous, the `manual` flag (you supply the material — found for a stock source, generated for an AI one), foreground inserts, described below.
- `llm/*.toml` — LLM connections (`provider`, `model`, `key_env`, `temperature`, `web_search`); the active one is named in `slopgen.toml` `[llm].profile`.
- `voices/*.toml` + the samples next to each one — cloned voices (`lang`, `default`, and one `[samples.<name>]` per delivery of that person, each with its own `ref`, `text` and optional `ref_url` for cloud cloning). Gitignored: this is a real person's voice, not a setting.

## Assets (`assets/`)

Drop files in, reference from configs:

```
assets/
  ads/<contract>/overlay/   # corner animations: .webm (alpha), .gif, .png
  ads/<contract>/native/    # pre-made ad video inserts
  music/                    # background tracks (one per run, mixed in at low volume)
  music/<shelf>/            # …in folders, if you want a run to draw from one kind only
  fonts/                    # extra subtitle fonts (passed to libass via fontsdir)
  footage/                  # local clips for the "local" footage provider
  footage/gameplay/         # background loops for the "gameplay" visuals profile
  images/                   # local pictures for photo backgrounds / foreground inserts
```

**The music** is one track per video, mixed under the voice at `[audio] music_volume`.
Which one is a setting like any other — `--music sweden.m4a` on the command line, a
dropdown on every start form, a column in the loop queue for one video of a batch, and
the same control in the montage room, where it also plays. Left blank it is drawn for
you, seeded on the run rather than on the clock: the same run always draws the same
track, which is what lets the montage room play the one the render will use. `none`
is silence.

Sort the folder into **shelves** and the draw narrows to one of them. Any subfolder of
`assets/music/` holding a track is offered as its own choice, written with a trailing
slash — `эпичное/` draws from that shelf, `эпичное/opening.m4a` is that one track, and
a track sitting loose at the root is still just `sweden.m4a`, which is why every choice
stored before there were shelves still resolves to the same file. Nesting is allowed
and a shelf covers everything under it. The draw is the same draw: seeded on the run,
so picking a shelf still lands the montage room and the render on the same track. A
shelf holding nothing is not offered — a control that can only be set to silence is
not a control — and one that has since been emptied rolls over everything instead.

**Bring your own content.** `assets/music/`, `assets/footage/`, `assets/ads/` and the personal `configs/` (`characters/`, `fandoms/` except the example world, `ads/*.toml` except the example, `accounts/`) are git-ignored on purpose — drop your own (copyright-cleared) tracks, clips, cast and worlds in. The repo ships only neutral templates: `configs/characters/example.toml`, `configs/fandoms/example/`, `configs/ads/example_vpn.toml`, and a few demo images.

Subtitles default to the **DejaVu Sans** font. It's preinstalled on most Linux distros; on Windows/macOS either install it or drop any `.ttf`/`.otf` into `assets/fonts/` and set `[subtitles] font` in `configs/slopgen.toml` to its family name.

**Pronunciation** (`[tts.pronounce.<lang>]` in `configs/slopgen.toml`). A few words come out wrong however they are spelled in the script: a synthesizer reads a Cyrillic acronym as a word whenever its letters happen to form a pronounceable syllable, so «НЛО» is said "нло" instead of being spelled out. It has to be an explicit list, because no rule separates the cases — the same reading is correct for «ВУЗ» and wrong for «НЛО». Everything else its Russian normalizer already handles (measured: «Лада-2107» and «18-летие» both come out fully expanded), so the table stays short and is yours to extend.

```toml
[tts.pronounce.ru]
"НЛО" = "эн эл о"
```

Separate the parts with **spaces**; hyphens do not work. Measured in running speech, «эн-эл-о» takes 0.26s — exactly as long as the broken «НЛО» — because the normalizer collapses a hyphenated run back into one syllable, while the spaced «эн эл о» takes 0.62s and is genuinely spelled out. Which spaced form reads best is per-word: bare letters «Н Л О» run 1.10s here, yet beat the phonetic names on other acronyms, so try both. The table is engine-independent by construction — every engine, including the aligner's recognizer, is fed the same respelled text. Only the voice sees it: the subtitles keep the original word, merged back from the pieces it was spoken as, with its exact start and end — so nothing is re-spread or estimated. This is the mirror of `--clean-subs`, where the voice keeps every word and only the burned-in text changes.

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

Опционально: `pip install -e '.[azure]'` — для движка озвучки Azure. Веса нейронок в зависимостях **не** лежат: их по требованию качает `slopgen models install …`.

<details>
<summary>Nix / NixOS</summary>

В репозитории есть `shell.nix` (Python 3.12 + ffmpeg + шрифты DejaVu):

```bash
nix-shell                        # при первом входе создаст и активирует .venv
pip install -r requirements.txt && pip install -e .
```

</details>

Личное и копирайтное вынесено в `.gitignore`: `assets/music/`, `assets/footage/`, `assets/ads/`, а также `configs/characters/`, `configs/accounts/`, `configs/fandoms/` (кроме мира `example/`) и `configs/ads/*.toml` (кроме `example_vpn.toml`). Занеси свои (правомерные) треки, клипы, персонажей и миры сам — в репозитории лежат только нейтральные шаблоны.

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

# Фандом: видео внутри мира, который ты сам описал, — рассказанное изнутри него как факт
slopgen fandom ru example --scenario "Что делают с сумками, за которыми никто не пришёл"
slopgen fandom ru example --narrator chronicler --duration-min 3 --parts 2

slopgen --resume output/<время>_<тип|режим>_<язык>   # продолжить оборвавшийся прогон

# в цикле: ролик за роликом, пока не остановишь
slopgen info ru facts --loop                          # темы придумывает нейронка, без предела
slopgen info ru --loop --loop-limit 20 --topics me    # двадцать штук, темы твои
slopgen info ru facts --loop --loop-ahead 5           # ...и пять тем наготове, их видно
slopgen loop topic "почему хлеб черствеет"            # рулить из другого терминала
slopgen loop queue  /  loop for 1,3 duration=90       # очередь и своя настройка у ролика
slopgen loop source ai   /   loop breaks script   /   loop limit 5   /   loop stop

# из браузера или с телефона: та же панель, те же прогоны
slopgen web                                           # http://127.0.0.1:8770
slopgen bot --detach                                  # Telegram: чат + мини-приложение
```

Одиночный результат: `output/<время>_<тип|режим>_<язык>/<n>/final.mp4`, а рядом
`metadata.json` — если прогон о нём просили (`--metadata`, см. ниже).
Многочастные дорамы складываются рядом в той же папке `<n>/` как `part_01.mp4`, `part_02.mp4`, ...

Первый позиционный аргумент — **режим**: `info` (ролик-минутка), `drama` (ИИ-дорама) или `fandom` (то же повествование, но внутри мира, который ты описал); он меняет остальную часть команды. Флаги драмы: `--scenario`, `--cast A,B` (имена из `configs/characters/`), `--orchestration`, `--duration-min` (минуты; `0` — длину выберет нейронка по премисе), `--tol` (секунды допуска), `--clip-s` (СРЕДНЯЯ длина клипа в секундах, 0 = длина самого генератора), `--visual-notes` (ограничения картинки, не сюжета), `--visual-style` (каким должно быть изображение — своими словами; компилируется в теги и дописывается к каждому промпту генерации, см. «Стиль графики»), `-F/--filter` (фильтр на весь смонтированный ролик — `bw`, `film`, `bloom`, `vignette`, `grain`, `vhs`, `crt`, `glitch`, доза 0-100; флаг повторяемый, см. «Фильтры»), `--clean-subs` (чистить мат в субтитрах), `--parts` (сколько частей с клиффхэнгерами просить у сценариста; границы потом двигаются на брейкпоинтах `script` и `cut`), `--parts-at-once` (резать все части в конце, а не по мере готовности), `--voice`, `--tts-engine`, `--tts-source`, `--deliveries` (интонации по строкам расставит нейронка — только для клонированного голоса, в карточке которого их несколько); плюс общие с `info`: `--ad`, `--ad-mode`, `--profanity`, `--push`, `-n`, `--subs`, `--out`, `--dry-run`, `--metadata` (сочинять заголовок, описание и теги готового ролика — **по умолчанию выключено**: это единственный этап, результат которого дальше по конвейеру никто не читает, и ролик, который ты просто посмотришь здесь, не должен платить за него модели; прогон, который реально публикует, метаданные получает всё равно — без заголовка ролик не зальёшь, — как и прогон с брейкпоинтом `metadata`), `--music` (что играет под голосом: трек по пути внутри `assets/music/` — `эпичное/opening.m4a`, — либо ПАПКА со слешем на конце (`эпичное/`), чтобы жребий тянул только с этой полки; `none` — тишина; по умолчанию тянет из всего, жребием, посеянным на прогоне, так что в монтажной слышно ровно то, что будет в рендере), `--keep-temp`, `--tts-rate` (скорость речи, ±50%; в дораме на неё рассчитывает сценарист — чем быстрее голос, тем больше сюжета влезает в клип той же длины, поэтому реплики пишутся длиннее), `-b/--break ЭТАП` (остановка на разбор после этапа, флаг повторяемый). Фандом (`fandom ЯЗЫК ФАНДОМ`) — это командная строка драмы с миром впереди: он берёт драмовские `--orchestration`, `--voice`, `--tts-engine`, `--tts-source`, `--deliveries`, `--visual-notes`, `--visual-style`, `-F/--filter`, `--tts-rate` и общие рекламно-публикационные, но НЕ берёт ни `--parts`/`--parts-at-once` (на серии он не режется), ни драмовских флагов длины: дораму авторят в минутах с допуском и средней длиной клипа, потому что её клипы покупаются у генератора с дневным лимитом, а этот ролик режется под длину, и длину каждого кадра выбирает сам сценарист. Свои флаги: `--duration СЕК` (длина готового видео в секундах, по умолчанию 120; `0` — нейронка прочитает бриф и решит сама), `--medium` (`video` — клипы, `photo` — слайд-шоу из неподвижных кадров; он же ограничивает `--source`) и `--source` (что делает кадры: генератор — `wan2.1`, `ltx-video`, `animatediff` для видео, `flux`, `turbo` для фото — либо `manual` (генерируешь ты) либо `search` (находишь ты); по умолчанию `wan2.1` для видео и `flux` для фото). Второй позиционный аргумент — имя папки в `configs/fandoms/`. `--scenario` здесь отвечает не на «что происходит», а на **о чём именно из этого мира рассказать** — обычай, место, человек, необъяснённый случай — или какую теорию по его записям отстоять; без него сценарист выберет сам. `--narrator` — кто рассказывает: `resident` (живёт там, от первого лица; дефолт) или `chronicler` (изучает записи мира и строит из них теории, без «я»-героя). `--invent` — насколько сценаристу позволено дописывать мир там, где кончаются записи: `no` (по умолчанию — записи и есть весь мир, пробел просто никому не известен), `gaps` (только там, где бит иначе не написать, и только самой мелкой бытовой подробностью) или `free` (дообставлять свободно); во всех трёх нельзя противоречить записям, придумывать предмет самого ролика, чеканить имя тому, что записи оставили безымянным, и разгадывать оставленное ими открытым. Каста в командной строке нет вовсе: персонажи мира лежат в `configs/fandoms/<имя>/characters/` и подключаются сами — персонаж в мире либо есть, либо его нет; человеком он быть не обязан и одним-единственным тоже: толпа одинаковых безликих и целая раса описываются так же — внешностью, а характеры остаются лору. У `-b/--break` появляется свой этап `canon`, но пропадает `cut`. У `info` есть свой `--visuals`. Глобальные (до режима): `--resume`, `--list-types/-ads/-accounts/-presets/-visuals/-characters/-orchestrations`. Управляющие подкоманды: `slopgen models list|install|remove|path` (нейронки: качает и удаляет) и `slopgen voices list|add|record|default|check|remove` (клонированные голоса: карточка — каталог подач одного человека, `default` называет ту, которой говорит ролик). Подкоманды `slopgen gather [папка]` и `slopgen review [папка]` возвращают к застывшему прогону — к материалу, который поставляешь ты (сделанному руками или найденному), и к брейкпоинту соответственно; без папки берётся последний такой прогон. `slopgen usage ПАПКА` печатает, во что прогон обошёлся по нейронке: токены по стадиям, сколько запросов было повторами, сколько входа провайдер отдал из кеша промптов и стоимость, если в профиле проставлены цены (`--calls` — по каждому запросу отдельно). Читается по чекпойнту самого прогона, поэтому отвечает по ролику там, где панель провайдера отвечает по суткам.

**Восстановление после сбоя.** Каждый прогон пишет чекпойнт в `output/<время>_<тип>_<язык>/checkpoint.json` после каждого этапа конвейера. Если прогон оборвался на ошибке (обрыв сети, убитый процесс), пройденная часть (озвучка, скачанный футаж, состояние задачи) сохраняется, а этап и текст ошибки записываются. Команда `slopgen --resume <эта папка>` пропустит выполненные этапы и продолжит с места остановки: готовые видео не трогаются, недоделанные досчитываются. Если прогон завершился с ошибками, в итоговой сводке печатается готовая команда `--resume`.

**Брейкпоинты.** Отметь любой этап конвейера на шаге **«Итог»** в визарде (или передай `--break ЭТАП`, флаг повторяемый) — и прогон встанет сразу после этого этапа: чекпойнт переводится в состояние `review`, а экран разбора показывает результат этапа списком редактируемых строк:

| Брейкпоинт  | Что показывает                                                                                        |
| ----------- | ------------------------------------------------------------------------------------------------------ |
| `idea`      | выбранную тему, ещё до единой написанной строчки                                                       |
| `canon`     | *(фандом)* канон-справку мира одним редактируемым блоком — поправить неверную дату, дописать обычай, который компилятор пропустил, вычеркнуть то, на что этому видео опираться не надо. Последний бесплатный момент починить мир: дальше каждое окно сценария пишется по этой справке, и чего в ней нет, того в мире нет. Правка остаётся при этом прогоне — в `configs/fandoms/` ничего не меняется |
| `script`    | сценарий как он написан — по сцене: реплика, промпт кадра (или поисковые слова), кто в кадре, нейронка и длина клипа. Единственное место, где кадр правится до генерации |
| `entities`  | *(дорама)* реестр визуала: всё, что повторяется в кадрах и не входит в каст — техника, локация, реквизит, безымянный завсегдатай, необычная массовка. По записи: имя, которым её называют промпты, заметка и английское описание, уходящее генератору. Правка одного описания меняет вид вещи сразу во всех кадрах |
| `tts`       | каждый озвученный фрагмент с получившейся длительностью; правь строку и **🔊 переозвучивай** прямо тут (▶ послушать), хоть до посинения — переозвучивается только она. Над кнопками **ползунок скорости**: он один на весь экран и применяется к тому фрагменту, который ты им переозвучил. Эта строка дальше живёт со своей скоростью (карточка её показывает), остальное видео остаётся на скорости запуска |
| `cut`       | *(дорама)* где кончается каждая серия. Сцены тут только для чтения, у каждой видно, сколько она реально идёт; двигаешь **маркеры частей** — это последний бесплатный момент для перекройки, дальше клипы генерируются (или просятся руками) уже под эти границы |
| `picture`   | *(фандом, база кадров)* дорожку картинки — и открывается она не списком, а **монтажной**: см. [монтажная](#монтажная) |
| `footage`   | промпт кадра (дорама) или поисковые запросы (инфа) по сценам; изменённым сценам видеоряд соберут заново |
| `subtitles` | сгенерированные `.ass` как текст, правки пишутся прямо на диск                                         |
| `assemble`  | готовые файлы — только просмотр, посмотри перед публикацией                                            |
| `metadata`  | заголовок, описание и теги перед самой публикацией. Сам этап выключен, пока о нём не попросят (`--metadata`), и галочка этого брейкпоинта — один из способов попросить: вставать ради чтения текста, которого никто не писал, незачем |

В терминале экран устроен как мастер-детейл: слева **карточки** позиций этапа — **＋** добавляет, **▲ ▼** двигают, **✖** удаляет, — справа поля открытой карточки. В браузере тот же документ рисуется одной колонкой карточек, у которых все поля открыты сразу, с теми же **＋ ▲ ▼ ✖** на каждой и перетаскиванием за ручку; всё сказанное ниже верно для обоих. (Некоторое время не было: браузер расплющивал документ в одинаковые неподписанные поля, в которых ничего нельзя было ни добавить, ни удалить, ни переставить, а маркер части показывался сырой строкой `bp.f.part`, — конвейер умел всё это с самого начала, не умел только браузер.) У сцены дорамы это реплика, промпт кадра, кто в кадре (добавляется и убирается поштучно из каста прогона), какая нейронка её генерирует и сколько длится клип. Поля правятся руками (кроме `.ass` — переписывание файла нейронкой снесёт тайминги), а под списком — ИИ-строка. На брейкпоинте `script` она работает со списком сцен целиком и умеет всё, о чём попросишь: переписать любое поле, переставить сцены, склеить, разбить, добавить, убрать, сменить каст и нейронки — сохраняя тождество сцен, так что нетронутая сцена оставляет при себе уже сделанные озвучку и клип. На остальных этапах переписывает текстовые строки: пишешь, что поменять («короче», «третью сцену злее», «разбей этот бит на два»), и модель переписывает весь набор; для сценария и озвучки она может ещё и поменять количество фрагментов. У дорамы среди карточек стоят ещё и **маркеры частей** — `── здесь начинается часть 2 ──`, — и всё, что ниже маркера, относится к этой серии. На `script` и `cut` их можно двигать (**▲ ▼**), добавлять (**＋ Разрыв части**, разрезая дораму дальше) и удалять (склеивая серию с предыдущей); на остальных этапах они нарисованы только для чтения, чтобы всегда было видно, какую серию смотришь. Именно этим и задаётся количество частей — `--parts` лишь просит у сценариста отправную точку. Жмёшь **Продолжить** — конвейер идёт дальше. Брейкпоинт срабатывает один раз на видео, так что переделка только что отредактированного этапа снова не встанет. Запаркованный прогон удаляется как любой другой, и второе нажатие говорит об этом прямо («он ждёт разбора — удалить вместе с папкой?»), а не спрашивает безликое «точно?»: один такой уехал в `rmtree` прямо из строки, которая просила его разобрать, — и виновата там была лёгкость нажатия, а не существование кнопки: запаркованный прогон обычно как раз тот, который и хочется убрать. Нумерация идёт **по месту**: удалил вторую из пяти — три нижние перенумеровались, чего раньше не было (подпись хранила номер, который блок имел при сборке документа, и после одного удаления две карточки могли обе называться #3; у рекламной вставки `#3 · AD` пометка остаётся, меняется только номер). Удалить можно **любой** блок, в том числе последний: запрет держался за «в документе должно что-то остаться» и стоял между тобой и самой частой причиной открыть этот экран — снести всё и написать самому. Форма последнего удалённого блока запоминается, поэтому **＋** соберёт новый и на пустом документе; а вот пустым документ не может быть в момент **применения** — там теперь и проверяется, вслух, вместо того чтобы конвейер молча оставил старые сцены. ИИ-строке теперь ещё и говорят, **где происходит видео**: на фандомном прогоне она получает ту же мировую рамку и ту же канон-справку, что и пишущие стадии. Без них она была единственным в прогоне, кто про мир не слышал, и читала написанные изнутри строки как обычные фразы — «расскажи про мой первый день на объекте» возвращалось стройкой с будильником и каской, потому что ровно это те слова и значат для всякого, кто не читал записей. При `-n` >1 видео выстраиваются в очередь и разбираются по одному. Работает во всех трёх режимах. В headless-прогоне печатается команда `slopgen review <папка>`, чтобы вернуться к застывшему прогону (по аналогии с `slopgen gather` для ручных клипов).

Приоритет параметров (режим info): **флаги CLI > пресет > дефолты аккаунта > глобальные дефолты**. Аккаунт может нести свои дефолты — `slopgen info --push yt_main` уже валидная команда. Драма собирает параметры прямо из своих флагов (слияния с пресетом/аккаунтом пока нет).

**Генерация в цикле (`--loop`).** Батч (`-n 5`) решает всё до старта: пять роликов, один
набор настроек, один источник тем — и сказать ему что-то после первого уже нельзя. Цикл —
та же работа по одному ролику, но решения оставлены открытыми: три из них перечитываются
перед каждым роликом, и все три можно менять на ходу. Добавь `--loop` к любому режиму
(`--loop-limit N` — предел, `0`/без флага — без предела; `--topics ai|me` — кто придумывает
тему; `--on-park hold|go_on` — что значит вставший на проверке ролик; `--loop-ahead N` —
сколько придуманных тем держать в очереди на виду). Тема, с которой цикл запущен, идёт в
первый ролик, кто бы ни придумывал остальные.

**Тема из очереди используется всегда, а источник решает лишь то, что будет, когда очередь
кончится:** при `ai` нейронка придумывает свою и цикл никогда не ждёт, при `me` — ждёт тебя.
Стоя в терминале, он СПРАШИВАЕТ: впиши тему следующего ролика, нажми Enter — и этот ролик
придумает нейронка, — или набери команду: `!ai` `!me` `!limit N` `!breaks script,tts`
`!park hold|go_on` `!ahead N` `!queue` `!stop`. Отовсюду ещё те же правки — это подкоманды,
и каждая попадает в следующий ролик, а не в середину начатого:

| Команда | Что делает |
| ------- | ---------- |
| `slopgen loop status [ПАПКА]` | чем занят, что сделал, что в очереди |
| `slopgen loop list`           | все циклы в папке вывода, свежие сверху |
| `slopgen loop queue [ПАПКА]`  | ещё не снятые ролики, по номерам, и что каждый просит для себя |
| `slopgen loop topic "..." [...]` | поставить темы в очередь (`--at N` — не в конец, а на это место; `--me` — заодно отдать тебе и остальные) |
| `slopgen loop edit N "..."`   | переписать тему одного ролика из очереди, его настройки не трогая |
| `slopgen loop move N КУДА`    | переставить ролик в очереди |
| `slopgen loop drop КАКИЕ`     | убрать ролики из очереди (`2` · `1,3` · `2-5` · `all`) |
| `slopgen loop for КАКИЕ КЛЮЧ=ЗНАЧЕНИЕ ...` | дать роликам из очереди **свой** ответ на настройку; `--clear КЛЮЧ` возвращает её циклу |
| `slopgen loop ahead N`        | сколько тем нейронка держит наготове в очереди (`0` — придумывать по одной, не показывая) |
| `slopgen loop source ai\|me`  | кто придумывает тему, когда очередь кончилась |
| `slopgen loop limit N`        | поставить предел или снять его (`0`) |
| `slopgen loop breaks [СТАДИИ]` | какие стадии встают на проверку со следующего ролика; без аргументов — никакие |
| `slopgen loop park hold\|go_on` | держит ли вставший ролик весь цикл |
| `slopgen loop set КЛЮЧ=ЗНАЧЕНИЕ ...` | **любая настройка генерации**, со следующего ролика (см. ниже) |
| `slopgen loop show`           | все настройки, на которых стоит цикл, и как называется каждая |
| `slopgen loop stop`           | закончить после текущего ролика; его самого никогда не рвут пополам |
| `slopgen loop go [ПАПКА]`     | поднять остановленный цикл в этом терминале, по его же плану |

Без `ПАПКИ` берётся тот цикл, в который писали последним. План лежит в
`output/loop_<время>_<тип>_<язык>/loop.json` и это обычный файл: рулить циклом может всё, что
умеет его править, — в том числе браузер, где цикл выглядит карточкой наверху вкладки
«прогоны» с теми же органами управления.

**Очередь и есть план, а запись в ней — целый ролик.** Не строчка текста: она несёт тему
*и* свои ответы на настройки везде, где хочет отличаться от цикла — длину подлиннее, один
брейкпоинт только на этот раз, без рекламы вот тут. Они накладываются на настройки цикла в
тот момент, когда до записи доходит очередь, а всё, на что запись не отвечает, остаётся
общим. Поэтому очередь из десяти — это десять уже решённых роликов, а не десять напоминаний
вернуться и перенастроить между ними. В этом весь смысл: набил, запустил, пошёл спать.

```bash
slopgen loop queue                                  # 1..N и своё у каждого
slopgen loop for 1,3 duration=90 breaks=script      # двум — подлиннее и с проверкой
slopgen loop for all fx=crt=40,grain=20             # всем, остальное не тронуто
slopgen loop for 2 --clear duration                 # №2 возвращается к общей длине
slopgen loop move 4 1   /   loop drop 2-5           # переставить или выкинуть
```

`for` берёт одну настройку и сколько угодно роликов намеренно. Правка, которая тащила бы за
собой целую форму, тащила бы и все настройки, которые ты менять не собирался, — и шесть
роликов молча вышли бы одинаковыми. Имена и значения те же, что у `loop set`, включая
короткие, а значение, которого настройки не удержат, отвергается **в момент набора**.

**Темы, придуманные заранее (`--loop-ahead N`, `slopgen loop ahead N`).** Цикл, придумывающий
темы сам, обычно придумывает каждую в момент, когда она понадобилась, — то есть её никто не
видит: о чём был ролик, узнаёшь из самого ролика. Поставь `ahead` — и нейронка вместо этого
держит столько тем **в очереди**, обычными записями: их можно прочитать, переписать,
переставить, дать им свои настройки или выкинуть до того, как они станут чем-то. Они
помечены как придуманные нейронкой, а сама нейронка знает, что уже стоит в очереди и что
этот цикл уже снял, — чтобы не выдать ту же тему дважды. Всё это по возможности и не
обязательно: молчащая нейронка стоит запаса тем и ничего больше, а цикл идёт дальше и
придумывает эту тему на этапе сценария, как делал всегда.

**Любая настройка на ходу.** Не только «кто придумывает тему»: всё, с чем прогон был
запущен, — настройка цикла, и что угодно из этого переписывается между двумя роликами:
длина, вид, монтажные фильтры, голос и его скорость, субтитры, каст, мир, цепочка
генераторов, рекламный контракт, куда публиковать. Набираются как `ключ=значение`, по
короткому имени или по имени поля:

```bash
slopgen loop set duration=90 style="плёнка, ранняя PS1" fx=crt=40,grain=20
slopgen loop set voice=ru-RU-SvetlanaNeural rate=-10 subs=karaoke clean_subs=yes
slopgen loop set cast=Алекс,Кирилл parts=3 tol=20        # дораме — сменить каст на ходу
slopgen loop set world=Хлябь narrator=chronicler source=wan2.1   # фандому — сменить прицел
slopgen loop set ad=example_vpn push=yt_main dry_run=no  # начать публиковать по-настоящему
slopgen loop show                                        # на чём он стоит прямо сейчас
```

`slopgen loop show` перечисляет всё вместе с коротким именем каждой. Значение, называющее
несуществующий конфиг — аккаунт, рекламный контракт, мир, фильтр, персонажа, — отвергается
**в момент набора**: цикл по замыслу работает без присмотра, и иначе об ошибке сообщит
ролик, упавший на последней стадии, пока ты спал.

Пять вещей настройками не являются, и у каждой есть своё место: **режим** (это другой
цикл), **количество** (повторяется сам цикл), **папка вывода** и **тема** — она в очереди,
см. выше. Всё остальное можно. В браузере это тот же набор и буквально та же форма: у
карточки цикла есть кнопка **настройки**, открывающая форму запуска этого режима,
заполненную из цикла, а кнопка запуска в ней читается как *применить к циклу*.

**В браузере очередь правится там же, где её читаешь.** Карточка цикла на вкладке «прогоны»
держит весь список: тащишь строку за ручку (или **↑ ↓**) — переставилась, пишешь в ней —
переписал тему, **×** — выкинул, **✨** — попросил у нейронки ещё три. **⚙** открывает
настройки этого ролика **прямо в строке**: длина, брейкпоинты и репетиция сразу, всё
остальное на одно нажатие ниже, под *ещё параметры*. Никуда не проваливаешься: ходить на
другой экран ради длины одного ролика — это ровно то, из-за чего очередь перестаёт быть
нужной. Параметр, на который ролик не отвечает, показывает значение цикла серым: тронул —
стало своим, нажал **↺** — снова общее. У закрытой строки на **⚙** написано число, так что
видно, отличается ролик от цикла или нет, не открывая его.

**Менять одну настройку сразу у многих** — та же панель с отмеченными роликами. Отмечаешь
строки галочками, и над ними появляется форма, где галочка стоит у каждого параметра:
записываются только отмеченные, всё прочее у этих роликов остаётся как было. Тронул
контрол — параметр отметился сам, а отмеченный уезжает наверх панели и подсвечивается,
чтобы то, что сейчас запишется, было коротким списком сверху, а не искалось заново среди
тридцати контролов. Там же — «дублировать» и «удалить» для отмеченных роликов.

**Правка попадает в следующий ролик и никогда в середину начатого.** Тот, который делают
сейчас, запущен на настройках, стоявших в момент его старта, — только так настройка может
означать одно и то же для целого ролика. План пишут сразу в несколько рук — цикл
записывает ролик, браузер переставляет настройки, терминал ставит тему в очередь, — поэтому
каждая запись берёт замок, перечитывает то, что лежит, и кладёт назад только свою половину.

**Ролики — обычные прогоны.** Они ложатся в папку вывода рядом со всеми прочими, поэтому
`slopgen review`, `slopgen gather`, список прогонов в TUI и в браузере доходят до них, не
зная про цикл вовсе. По умолчанию вставший ролик — на брейкпоинте или в ожидании ручных
клипов — **держит** цикл, пока ты им не займёшься: запуск следующего ровно тем и мешает, что
не даёт доделать тот, который задал тебе вопрос. `--on-park go_on` — идти дальше несмотря ни
на что. Ещё цикл останавливает себя сам после трёх падений подряд: иначе цикл без предела и
умерший ключ API провели бы ночь, падая по кругу.

**Цикл переживает перезапуск сервера.** План — это файл (`output/loop_*/loop.json`), и он
живёт дольше процесса ровно так же, как чекпойнт. Но какое-то время при старте с диска
поднимались только *прогоны*, а циклы — нет: перезапуск веб-сервера опустошал вкладку с
циклами, хотя все планы лежали нетронутыми, — очереди, настройкам каждого видео и всей
панели управления просто не на чем было держаться. Теперь поднимаются оба. Цикл
возвращается **остановленным** и сам никогда не продолжается: сервер чаще всего
перезапускают именно чтобы что-то встало, и перезапуск, который втихую подхватил бы шесть
видео из очереди, — это обратное тому, о чём просили. План, в файле которого всё ещё
написано `running`, тоже помечается остановленным: поток, написавший это слово, умер
вместе с предыдущим процессом. На поднятом цикле работает всё, что есть на карточке:
очередь, ⚙ у каждой темы и **настройки цикла** — там же и меняется не тот мир.
У остановленного цикла есть ещё **продолжить цикл** и **удалить цикл**: ни того, ни
другого не было, пока каждый цикл на странице был только что созданным из визарда, —
«остановлен» и «исчез» выглядели одинаково, и карточке хватало кнопки «стоп».
«Продолжить» поднимает план как есть: оставшиеся темы, те же настройки, тот же счёт, —
и никогда не переделывает уже готовые видео (`LoopFile.restart` снимает флаг остановки
и серию падений, которой цикл кончился: они лежат в файле, и поток, запущенный без
их сброса, прочитал бы их и тут же кончился снова). «Удалить» уносит папку плана и
**оставляет все сделанные циклом видео** — это обычные прогоны в своих папках, и
половина причин выбросить план в том, что очередь была не та, а выхлоп нормальный.
Спрашивает дважды, как и список прогонов.

**В очередь кладётся ТЕМА** — одна строка, десяток слов, «Как попасть на Объект?» — а не
бриф. Фандомный цикл раньше набивал очередь абзацами, и это хуже, чем просто неопрятно:
сценарист читает короткий бриф как тему, вокруг которой строить ролик, а длинный — как сам
ролик, — так что очередь абзацев была очередью роликов, которых никто не утверждал, и
каждый решал, чем он будет, ещё до того, как его прочли. Причина — поле, делавшее две
работы. `write_brief` отдаёт форму и длину на откуп инструкции оператора, без потолка (в
визарде это правильно: инструкция там оператора и есть), — а цикл тоже передаёт инструкцию:
машинную «придумай свежую тему, не из этих». Одной своей непустоты хватало, чтобы включить
режим «без потолка», и очередь наполнялась готовыми сценариями. Теперь тот, кому нужна тема,
говорит это отдельным аргументом, и он старше инструкции, которую сам же и написал.

**Стоп и остановка.** «Стоп» заканчивает цикл после текущего ролика — его самого
никогда не рвут пополам, потому что порванный пополам потратил квоту и не дал ничего.
Значит, остановленный цикл работает ещё столько, сколько идёт этот ролик, — и карточка
теперь так и пишет («остановлен — доделывает начатый ролик»), а не показывает `running`
под уже нажатой кнопкой. Всё дело было в этом молчании: флаг записывался в план, никто
его не показывал, и оставалось единственное прочтение — кнопка не работает. Рядом со
словами стоит то единственное, что кончает всё раньше, — **«бросить и текущий ролик»**:
останавливает и сам прогон, спрашивает дважды, потому что выбрасывает уже оплаченную
работу, и вынесено в отдельную кнопку ровно затем, чтобы «стоп» продолжал значить то же,
что значил всегда. А кнопка запуска теперь срабатывает один раз, сколько бы нажатий ни
прислала мышь: двойной клик заводил два прогона — или два цикла с одной и той же
очередью, каждый со своим потоком.

**…и что раньше могло значить «после текущей стадии».** Прогон просят остановиться
флагом, и флаг читался МЕЖДУ стадиями. Для конвейера из коротких шагов это верное место;
для длинных — не место вовсе. Стадия футажа с семью сгенерированными фонами — это семь
заходов в чужую очередь, а у `gradio_client` нет таймаута, и у сокетов под ним тоже, —
поэтому Space, который принял задание и не ответил, держал стадию, стадия держала
прогон, а страница продолжала писать «остановится после текущей стадии» про стадию,
которая кончаться не собиралась. Замерено на том прогоне, из-за которого это и
написано: три часа на одном кадре из семи, соединение с той стороны уже закрыто, и
прекратить это нельзя ничем, кроме перезапуска сервера. Поменялось двое. Длинная стадия
теперь спрашивает `ctx.stopping()` на каждой сцене и бросает `Stopped`, а он паркует
прогон ровно так же, как ответ между стадиями, — именно бросает, а не возвращает:
вернувшаяся стадия считается законченной, и футаж, заполнивший две сцены из семи, отправил
бы прогон собираться из пустоты. И один вызов в Space стал ограниченным по времени
(`SLOPGEN_SPACE_TIMEOUT_S`, по умолчанию 600 с, `0` — прежнее ожидание без границы):
с запасом, потому что Space в очереди честно думает минутами, а таймаут, сработавший на
живом, стоит клипа впустую.

**Длина на усмотрение нейронки (`0`).** Поставь `0` там, где задаётся длина, — `--duration`, `--duration-min` или поле «Длина» в TUI, — и её никто не покупает: модель выбирает её по материалу. Решает бриф: бриф, который уже готовый текст, идёт ровно столько, сколько его произносить; премиса с тремя поворотами получает время, которое этим поворотам нужно; голая тема получает то, что положено формату. Берут это все режимы, и приходят к этому по-разному, и не случайно. Инфо-ролику дополнительный запрос не нужен вовсе: его сценарий — один вызов, а видео идёт ровно столько, сколько вышло озвучки, так что сценаристу просто говорят выбрать самому. Дораме и фандому надо знать заранее: из длины нарезается список кадров, а число кадров решает, за сколько проходов пишется сценарий, — поэтому прогон делает один маленький запрос, который читает бриф и отвечает секундами, печатает, что выбрал и почему, и дальше идёт ровно так, как если бы это число вписал ты, вместе со всеми проверками бюджета. Держать за длину не менее строго оттого, что её выбрала сама модель.

## TUI

`slopgen` без аргументов. Тема **Minecraft**, нижней панели нет — сверху панель с переключателем языка интерфейса RU/EN, кнопкой `<-` (назад) и Palette.

- **Меню** — по центру, выбор стрелочками + Enter.
- **Генерация** — сначала выбор режима (**минута инфы**, **ИИ-дорама** или **Фандом**), затем пошаговый визард со списком шагов слева. *Info:* 1) контент (язык, голос диктора, тип, идея, ползунки мата и скорости речи), 2) видеоряд (профиль + переопределения: фон, привязка, интервал, Ken Burns, вставки; длительность) — в том числе тумблер **Фон беру на себя** и такой же для вставок, и что это значит, следует из выбранного выше источника: сток превращается в поиск, на который слопген напишет тебе задание, ИИ — в генерацию, для которой слопген напишет промпт, 3) реклама (контракт *или* вручную), 4) публикация (аккаунт, количество, стиль сабов, переключатель чистых субтитров), 5) итог с CLI-командой, тумблерами брейкпоинтов и кнопкой СГЕНЕРИРОВАТЬ. *Дорама* добавляет шаг **Сюжет** (замысел + переставляемый каст, редактирование справа, фото→внешность через vision, ИИ-заполнение каста), кладёт длину клипа, скорость озвучки и ограничения картинки на **Контент**, количество частей — в **Публикацию**, и превращает шаг «Видеоряд» в **оркестрацию** (упорядоченный список ИИ-генераторов; см. ниже). *Фандом* заменяет «Сюжет» двумя своими шагами. **Мир** держит всё, что принадлежит месту: какой фандом, его персонажи списком слева и правкой справа, и документы лора прямо тут — markdown-исходник при наборе, отрисовка с прокруткой по кнопке ✎/👁, — под ними канон-справка с кнопкой пересборки. Персонаж здесь — это всё, что мир может показать и что должно выглядеть одинаково дважды: человек, но с тем же успехом существо, машина, судно или место, ведущее себя как персонаж, — человеческая форма нигде не предполагается, от карточки в списке до промпта, который компилирует внешность. И единственность тоже нигде не предполагается: каждая запись говорит, конкретный это персонаж, много одинаковых безликих или целый вид, — и это единственное поле кроме имени и внешности, потому что персонаж мира и есть ВНЕШНОСТЬ, а характеры лежат рядом, в лоре. Каждый — файл в папке самого мира: 💾 записывает его в мир, 🗑 убирает оттуда. Персонаж в мире либо есть, либо его нет, поэтому добавить кого-то на одно видео нельзя, и своего каста у прогона нет — конвейер читает их прямо у мира. **Замысел** после этого — про само видео: кто рассказывает (житель или летописец), о чём оно или какую теорию доказывает, и помощник ИИ, который пишет этот замысел по миру и не трогает больше ничего. Помощник читает сами записи, а не собранную справку (строка на вещь — ровно то место, где две одноимённые институции перестают различаться), знает, сколько идёт видео, — в том числе то, что длину никто не назвал, и тогда её решает его же замысел, — а форму и длину ответа задаёт твоя инструкция, без потолка: попросишь тему — получишь две фразы, попросишь расписать всё целиком — получишь целиком, и стадия сценария прочитает это как сам ролик, а не как тему. Четвёртый шаг здесь — тоже не драмовский редактор цепочки: он спрашивает, **откуда берутся кадры** (🤖 ИИ-генерация с выбором генератора, 🙋 генерирую сам, 🔍 ищу сам), и собирает за ответом одноэтапную цепочку сам. Мир идёт первым намеренно, и дальше шага «Мир» визард не пустит, пока мира нет.
- **Прогресс** — пока идёт прогон, полоса показывает, где он внутри этапа: фрагменты озвучки, сгенерированные видеофрагменты, смонтированные сцены и собранные файлы, каждое как `сделано/всего`, над таблицей очереди по роликам и живым логом. **Теперь та же полоса есть и в браузере**, прямо в строке прогона: название этапа, счётчик и шкала. Счётчик всё это время ехал по проводу (`Run.progress`), но вешать его было не на что — никто не записывал, КАКОЙ этап считает, — поэтому прогон в браузере был словом статуса и логом, и отличить медленный этап от зависшего можно было только чтением лога. Теперь этап хранится на прогоне и отправляется тем, кто смотрит, — по тому же потоку, что несёт лог, но под собственным именем этапа, так что тик двигает шкалу, не попадая в лог и не тратя весь буфер событий на восемьдесят четыре клипа дорамы. Повторные тики отбрасываются, начавшийся этап обнуляет шкалу (чтобы счёт закончившегося не стоял под именем следующего), а этап, в котором считать нечего (сценарий, описание), получает имя и бегущую полосу вместо выдуманных процентов. Полоса живёт только пока прогон идёт: полная шкала над упавшим прогоном была бы враньём мебели.
- **Разбор** — туда, где прогон встал: экран брейкпоинта (слева карточки, справа поля открытой, под ними ИИ-строка) либо экран сбора материала от оператора. Поисковую задачу экран сбора рисует тем, что она есть, — поручением, а не промптом для вставки: в шапке написано «найти самому» и что просится, фото или видео, рядом с длиной кадра в секундах; под шапкой бриф, а под ним запросы, по одному на строку, чтобы копировать их по очереди, пока какой-нибудь не сработает. В списке поисковые строки помечены ещё и `[фото]`/`[видео]`. Задача на генерацию так и остаётся одним куском промпта. Оба экрана по завершении продолжают прогон; `slopgen review` / `slopgen gather` открывают их напрямую.
- **Конфигурация** — секции слева: профили нейронок (табы профилей, пресеты моделей, ввод API-ключа с автосохранением в `.env`, активация ★), ключи стока и генераторов, библиотека персонажей, фандомы (настройки мира, его лор в том же редакторе, его собственный каст), рекламные контракты, аккаунты, пресеты. В секциях сущностей сверху табы — по одному на конфиг-файл плюс `+ новый`; формы предзаполнены, есть 💾 сохранение и 🗑 удаление с подтверждением.
- Выбранная тема оформления сохраняется между запусками (`[ui].theme`).

## Бот в Telegram и мини-приложение (`slopgen bot`)

```bash
pip install -r requirements.txt         # ничего нового: бот ездит на httpx
# 1. заведи бота у @BotFather, положи токен в .env:
#      TELEGRAM_BOT_TOKEN=123456:AA...
# 2. (не обязательно, но нужно) поставь cloudflared — именно он даёт мини-приложению
#    https-адрес:  https://github.com/cloudflare/cloudflared/releases
slopgen bot                             # в терминале
slopgen bot --detach                    # ...или в фоне, терминал свободен
slopgen bot --status                    # работает? где панель?
slopgen bot --stop
```

Напиши ему — он ответит твоим же Telegram-id; впиши этот id в `configs/bot_allow.txt`
(по одному в строке, `#` — комментарий, **первый** id — владелец), и он начнёт с тобой
разговаривать. Файл перечитывается при изменении — добавить человека это правка, а не
перезапуск, — и **кого нет в списке, тому не отвечают**: ни в чате, ни в мини-приложении.

**Мини-приложение — это и есть морда в браузере, а не её копия.** Кнопка открывает ту
самую страницу, которую отдаёт `slopgen web`, — внутри Telegram, на телефоне. Пароль не
нужен: Telegram кладёт странице подпись, которую умеет посчитать только держатель токена
бота, сервер её проверяет, а потом сверяет id с тем же списком допущенных. Одно следствие
стоит знать: пока панель отдаёт бот, анонимного входа у неё больше нет — с паролем или
без.

**Адрес бесплатный и одноразовый.** Telegram открывает мини-приложение только по HTTPS,
а у машины под столом его нет — поэтому бот поднимает быстрый туннель Cloudflare
(`cloudflared tunnel --url`) и берёт то имя `*.trycloudflare.com`, которое вернут. Оно
меняется при каждом перезапуске, и стоит это ровно одного сообщения: кнопка строится из
текущего адреса, а владельцу приходит уведомление, когда адрес переехал. Есть свой домен
— впиши его в `[bot].public_url`, и туннель не поднимется вовсе.

**Почти всё работает и в чате**, а кое-что там и должно жить. Запустить прогон — выбрать
режим и мир (`/new`); все остальные настройки будут такими, как их описали конфиги,
потому что параметры собирает тот же код, через который проходят формы браузера. Смотреть
за ними — `/runs`: остановить, снять с брейкпоинта, получить готовый ролик **прямо в чат**
видеофайлом. И главное — отдавать материал: конвейер просит у человека картинки в
непредсказуемое время, и застывший прогон присылает каждый недостающий кадр отдельным
сообщением — ответь на него фотографией или клипом, и файл ляжет во входящие прогона под
id этого кадра, ровно туда же, куда его кладут `slopgen gather` и загрузка из панели.
Отправишь **файлом**, а не фото — качество не пострадает от сжатия.

| `[bot]` в `configs/slopgen.toml` | что делает |
| --- | --- |
| `token_env` | в какой переменной окружения лежит токен (по умолчанию `TELEGRAM_BOT_TOKEN`) |
| `allow_file` | список допущенных; первый id — владелец |
| `web` | отдавать панель тем же процессом — её и открывает мини-приложение |
| `tunnel` | `cloudflared` или `off` |
| `public_url` | свой https-адрес; задан — туннель не поднимается |
| `deliver_video` | слать готовый ролик в чат (до `max_upload_mb`; Telegram не даёт боту больше 50) |

## Выкат на сервер (`./deploy.sh`)

Debian с systemd, один скрипт, без контейнеров.

```bash
cp deploy.env.example deploy.env        # SSH_HOST и SSH_USER, больше ничего
./deploy.sh bootstrap                   # пакеты, служебный юзер, venv, cloudflared, юнит
./deploy.sh setenv TELEGRAM_BOT_TOKEN 123456:AA...
./deploy.sh allow 123456789             # твой Telegram-id
./deploy.sh restart && ./deploy.sh url  # где панель прямо сейчас

./deploy.sh push                        # новый релиз; переключится, только если импортируется
./deploy.sh rollback                    # вернуть предыдущий
./deploy.sh status | logs | pull        # что живо · журнал · забрать готовые ролики
./deploy.sh run -- --list-types         # запустить CLI на сервере, с теми же конфигами
```

Выкат не копирует файлы поверх работающего кода: он собирает релиз рядом, проверяет, что
тот импортируется тем самым venv, которым его будут запускать, переключает симлинк
`current` и перезапускает сервис — а если тот не поднялся, тем же заходом возвращает
симлинк назад и перезапускает снова. Поэтому откат — это `ln -sfn`, а не «восстановить из
бэкапа, которого нет».

Состояние никогда не ездит с кодом. `configs/`, `.env`, `assets/`, `output/` и `models/`
лежат в `/opt/slopgen/.state` и остаются серверными: список допущенных, токен бота и
готовые ролики правились *там*, и выкат, затирающий их, был бы способом однажды выключить
бота. `./deploy.sh seed` заливает локальные поверх серверных нарочно, руками, и он
единственный это делает.

## ИИ-дорама (`configs/characters/`, `configs/orchestration/`)

Второй режим: **озвученная веб-дорама** — один закадровый рассказчик ведёт историю (и может цитировать реплики героев внутри повествования) поверх ИИ-кадров с постоянным кастом.

- **Каст** (`configs/characters/*.toml`): `name`, `age`, `appearance`. Перед генерацией каждый персонаж один раз компилируется в токен-плотный английский `visual_prompt`, который подставляется **вместо имени персонажа** там, где описание кадра его упоминает: имена до генератора не доходят (он не свяжет имя с лицом, а кириллицу отрисует текстом поперёк кадра), а привязка описания к тому, кто действует, не даёт смешать или перепутать двух персонажей в одном кадре — и внешность держится (это текстовый якорь; бесплатные генераторы не фиксируют лицо идеально). В TUI можно собрать каст ad-hoc, подтянуть из библиотеки, загрузить фото (vision → внешность) и дать ИИ заполнить весь каст по замыслу.
- **Реестр визуала** (этап `entities`): каст фиксирует, как выглядят *люди*; всё остальное, что история переиспользует, не фиксирует ничто. Названные один раз в одном кадре и один раз в другом, дом-трансформер, конкретная машина или кухня, в которой всё происходит, каждый раз рисуются заново — отсюда и «robot-house», приезжающий обычным роботом. Поэтому, как только сценарий готов, проход по всем промптам кадров собирает всё, что встречается **больше чем в одном кадре** и не входит в каст, и описывает это один раз. Реестр намеренно **нетипизированный**: что заносить, решает модель — техника, здание, реквизит, повторяющийся человек, не попавший в каст, и даже массовка, если она особая (форма, плакаты), а не просто прохожие; а `kind`, который модель пишет, — ярлык для чтения глазами, на него ничто не завязано. Дальше описание записи подставляется вместо её имени ровно так же, как у персонажа. Тот же проход делает промпты пригодными: кадр, показывающий занесённую вещь, обязан называть её реестровым именем; каждый персонаж, которого сценарист отметил присутствующим, должен быть в промпте **назван** (в неназванного нечего подставлять, и внешность оказывается дописана в конец, не привязанная ни к кому); и каждый промпт обязан сказать, **где** это происходит — промпт из одних людей и глагола отрисовывается как эти люди, стоящие в пустой комнате. Весь реестр смотрится и правится на брейкпоинте `entities`; одно описание меняет вид вещи во всех кадрах сразу.
- **Бюджет промпта**: внешность не должна забивать кадр. Три персонажа по ~20 тегов описания оставляют от действия погрешность округления — генератор рисует троих по описаниям, и на то, что они делают, у него уже ничего не остаётся. Поэтому общий кадр делит один бюджет внешности между теми, кто в нём: двое получают по 6 тегов, трое — по 4, при нижней границе в 3 тега, которая держит лицо узнаваемым между кадрами. Одинокий персонаж не урезается никогда, как и запись реестра: её описание — единственное, что объясняет, как выглядит выдуманное слово. Персонаж, упомянутый в кадре только как владелец (`Игнат's robot-house`), в кадре не находится и внешности не получает — иначе мужик с поясом инструментов приклеивается к зданию.
- **Оркестрация** (`configs/orchestration/*.toml`): упорядоченный список ИИ-генераторов — `model` (`wan2.1`/`ltx-video`/`animatediff` — видео, `flux`/`turbo` — картинка), `key_mode` (`rotate` — ротация ключей на лимите / `single` — один ключ, потом пропуск), `metric`+`amount` и необязательный `clip_seconds`. Конвейер идёт по этапам, каждый делает свою долю клипов: `percent` — доля бюджета длины, `seconds`/`clips` — абсолютный кусок, последний этап добирает остаток. Несколько API-ключей (по одному на строку в `.env`) ротируются между этапами. `clip_seconds` этапа перебивает общее значение прогона — пригодится, когда клипы одного этапа длиннее остальных (ручные кадры из Kling/Veo рядом с пятисекундными клипами Spaces). Одно имя в `model` — **не генератор**: `manual`, каждый клип делаешь руками ты. Долю он берёт ровно так же, как генератор, поэтому цепочка может отдать первую минуту Wan, а остальное тебе — или подмешать найденные съёмки в сгенерированную дораму, и механика долей и порядка разницы не заметит. В TUI они называются 🙋 генерирую сам и 🔍 ищу сам.
- **Длина, части и синхрон**: задаётся в **минутах** + **допуск** в секундах (история может немного выйти за рамки). Если частей больше одной, **обрывы планируются заранее** — номерами битов, тем самым проходом-планом, что ниже, — и окно, внутри которого приходится обрыв, узнаёт, какой из *его собственных* битов закрывает серию: этот бит пишется клиффхэнгером, а следующий открывается прямо с последствий. В какую серию попадёт бит, решает план, а не метка, которую угадал сценарист. **Длина клипа** тоже задаётся (`--clip-s` или поле на шаге «Контент» в визарде; 0 = номинал генератора, ~3-5с). От неё зависит, на сколько клипов режется бюджет и сколько озвучки достаётся каждому: семиминутная дорама — это 84 клипа по 5с, но всего 28 по 15с. Длина **фиксирована** — сценарист её не меняет, а только под неё пишет. Варьируется то, сколько сюжета влезает в бит: затяжной момент ему велено растягивать на несколько подряд идущих битов, а не запихивать в один, а быстрый поворот — сжимать в один. Набивать бит тремя несвязанными действиями ради заполнения времени — это и читается монотонно. Сценарий пишется **окнами** по ~14 битов, а не целиком за раз: когда у модели просят весь полнометражный сценарий одним ответом, её внимание уходит в начало — первые биты идут по замыслу фраза за фразой, а дальше начинается пересказ, и из него выпадают названные тобой предметы, побочные линии и повороты. Но одних окон не хватило — и именно тут длинная дорама разваливалась после середины: если окну сказано лишь, что его биты идут «примерно с 55% замысла», ему приходится на глаз прикидывать, какие это фразы в брифе на две тысячи слов, и прикидывает оно плохо — средние окна пересказывают то, что уже рассказало раннее, пропускают всё, что между, и вторая половина идёт уже не по твоему брифу, а по тому, что модель запомнила. Поэтому бриф режется **сначала**, отдельным **проходом-планом**, который читает его целиком за один запрос: на каждое окно он фиксирует, что в этом отрезке происходит, список конкретных деталей брифа, за которые отвечает именно этот отрезок и никакой другой (имена, числа, предметы, места, произнесённые реплики), и то, в каком положении окажется история к его концу. Дальше каждое окно пишет по своему отрезку, держа перед глазами весь план целиком — чтобы видеть, что уже рассказано, а что ждут от него следующие окна. Тот же проход выбирает, где резать серии. Окна сбалансированы, так что 30 битов — это 15+15, а не 14+14+2: окно из двух битов — это целый запрос, которому велено уместить финал в два бита. Дорама в одно окно видит всё сразу и план не заказывает; так же поступает и прогон, у которого план вернулся негодным, — он откатывается на процентные отрезки. Учти, что одни только **минуты** детализации не добавляют: сколько влезает в бит, определяется **длиной клипа** — озвучка нарезается по битам (6с ≈ 12 русских слов на обычной скорости, одно короткое предложение). Плотному на детали сюжету нужны клипы подлиннее, а не просто хронометраж побольше. **Скорость озвучки** (`--tts-rate`) входит в эту арифметику, а не приделана сбоку: бит, озвученный на +30%, требует на треть больше слов, чтобы заполнить тот же кадр, — поэтому сценаристу называют бюджет слов на той скорости, на которой прогон реально заговорит, а подгонке голоса и картинки ниже остаётся только остаток рассогласования. Отдельный фрагмент при этом можно переозвучить на своей скорости на брейкпоинте `tts`. При любой длине бит остаётся ОДНИМ непрерывным дублем, а не списком склеек: если написать `общий план THEN крупный план THEN реакция`, реальные генераторы читают это как раскадровку и открывают клип сеткой из всех кадров сразу. К каждому промпту дописывается явное «один кадр целиком» — генераторы тянутся к сетке и без просьбы. Один бит равен одному клипу, и подгоняются они ступенями, начиная с самого дешёвого. **Голос** двигается раньше картинки — его никто не видит: до ±25% он забирает рассогласование целиком, клип идёт нетронутым. Дальше голос встаёт на комфортной границе, а остаток берёт **картинка**, замедляясь вплоть до 45% скорости (читается как намеренное слоу-мо); и только когда исчерпана и она, голос дожимается до жёсткого предела. Реплика 20с на клипе 15с — только голос; 45с — голос 1.35× поверх клипа 0.45×, и они всё ещё сходятся ровно. Обратная сторона несимметрична: если голос **короче** клипа, не трогается ничего, а лишний хвост картинки просто обрезается — это не стоит ничего и не видно, а ретайм там купил бы вялый голос или комичный клип и всё равно обрезал бы следом. Раньше всё вешалось на голос, клипа не хватало, и он **начинался сначала посреди сцены**. Тайминги субтитров пересчитываются — звук и видео синхронны. Нативная реклама вплетается в сюжет на уровне сценария, а не вклеивается отдельно.
- **Твой сюжет режут, а не переписывают**: что сценаристу позволено делать с `--scenario`, зависит от того, что ты туда положил, и теперь ему это сказано прямо, а не оставлено на догадку. Где бриф **уже написан** — законченные фразы, твоими словами, со своим содержанием — работа только одна: **разрезать его на биты**. Твои формулировки, твой порядок слов, твои термины и имена, твои шутки, твой порядок событий. Ничего не дописывается — ни причина, ни следствие, ни реакция, ни прилагательное — и ничего не выкидывается. Где что-то **и правда сломано** (противоречит себе или листу каста, имя употреблено в двух видах, фразу не выговорить) — чинится ровно это, минимальной правкой, а всё вокруг остаётся как было; фраза проще, чем написала бы модель, повтор, резкий переход, необъяснённая деталь или концовка без развязки ошибками **не** считаются и не трогаются. Выдумывает он только там, где бриф — **наброски**: названа тема, список, кусок, через который бриф перешагнул; и вот там выдумывает свободно. Все три случая решаются пофразно, потому что сюжет обычно в одном абзаце готовая проза, а в следующем — пункт списка. Если бриф короче хронометража, разница уходит на то, чтобы подробно сыграть перешагнутые моменты, а не на расширение уже написанных фраз, — и если хочется, чтобы историю придумали, надо писать меньше, а не длиннее объяснять. Правило держит и проход-план, так что отрезок не *планируется* вокруг события, которого ты не писал. В режиме фандома всё то же самое, и записи мира там — ограничение на то, как можно рассказать твой бриф, а не материал, которым затыкают в нём дыры.

- **По одной серии за раз**: часть — это самостоятельное публикуемое видео, и конвейер доводит её до конца сразу, как может, а не ждёт всю дораму. В этом и смысл user-assisted пути, где клипы гонишь руками в каком-нибудь веб-сервисе, а бесплатные дневные лимиты кончаются сильно раньше сюжета: собрал клипы первой серии, продолжил — и она смонтирована, засубтитрена, описана и опубликована, пока вторая и третья лежат нетронутыми. Пришёл завтра с новыми клипами, `slopgen gather` — и подхват ровно оттуда: готовые части не пересобираются, их клипы не перегенерируются. У каждой части свой таймлайн (её субтитры начинаются с 0:00), свой файл (`part_02.mp4`), свой `metadata_part_02.json`, написанный моделью, которой сказали, какую серию из скольких она описывает, и уходит она в публикацию сразу, как готова. Загрузка отмечается на самой части, так что сколько ни продолжай — дважды не зальётся. На экране сбора появляется колонка **часть**, а по каждой серии — либо `часть 1 ✔ можно монтировать`, либо сколько клипов ей ещё не хватает: **Завершить и продолжить** требует одной готовой серии, а не всех. Продолжать при этом ничто не мешает — пока часть рендерится, генерируй дальше и клади в `manual/inbox/`, следующий резюм их подберёт. Если хочется, чтобы дорама вышла целиком разом — или чтобы сначала посмотреть всё, а потом уже публиковать, — выключи тумблер **Доводить части по одной** (`--parts-at-once`), и ничего не смонтируется, пока не приедет последний клип: ровно как было раньше.

- **Цензура и кадрирование**: два переключателя, чтобы прогон дожил до публикации. **Ограничения картинки** (`--visual-notes`) связывают только то, что показывают, и больше ничего — сюжет пишется так, будто их нет, поэтому «всё оружие игрушечное» оставляет перестрелку перестрелкой и меняет лишь реквизит; они уходят сценаристу и дописываются к каждому промпту. **Чистые субтитры** (`--clean-subs`) заменяют мат в вожжённом тексте, оставляя его в озвучке — платформы модерируют то, что могут прочитать. Переписываются целые реплики, а не отдельные слова: «Съебал нахуй с моей пары пидорас блять» → «Уйдите пожалуйста с моей пары молодой человек» — пословная замена оставила бы хромающее предложение. Ловит и слова, которые лишь похожи на мат, вроде первой части имени «Хуй Сунь Вынь», а поскольку переписывание меняет количество слов, интервал реплики заново делится между новыми словами, чтобы она по-прежнему начиналась и кончалась вместе с речью. Расход ограничен: сначала реплики просеиваются регуляркой, поэтому на чистом видео запроса не будет вовсе, а уходят только помеченные (каждая с ближайшими соседями для контекста) одним запросом на видео. Ответ размечен номерами строк, так что частичный ответ доносит то, что вернул, а маскируются лишь остальные.

Запуск из TUI (Генерация → ИИ-дорама) или headless: `slopgen drama ru --scenario "…" --cast example --duration-min 2 --tol 20 --parts 3 --orchestration my_chain`.

## Фандом (`configs/fandoms/`)

Третий режим. **Фандом** — это выдуманный мир, который ты записал в markdown; дальше сценарист рассказывает видео, происходящее в нём, **считая этот мир тем самым настоящим, в котором он живёт**, — а не выдумкой, которую он пересказывает. В этой позиции весь смысл режима, потому что по умолчанию модель делает ровно обратное: получив документ с миром, она начинает его *объяснять*. «В этой вселенной…», «автор нигде не уточняет…», «фанаты давно предполагают…», «в отличие от нашего мира…» — и на выходе видео про документ, а не видео с места. Поэтому контракт промпта эту позицию отбирает, и отбирает по пунктам: каждый способ сломать иллюзию — отдельный рефлекс, будь то называние медиума, упоминание автора, обращение к аудитории фанатов или ссылка на наш мир как на систему отсчёта. Сравнивать не с чем — внешнего мира нет. А там, где записи молчат, молчит не мир: что-то там **есть**, просто оно неизвестно, оспаривается, забыто или намеренно не записано, — и в этом разница между «в книгах учёта не сходится» и «в лоре нестыковка». Пробел в лоре — это пробел в том, что **известно**, а не в том, что кто-то написал. Всё, чем сценарист этот пробел заполняет, обязано быть той же породы, что и остальной мир: ни предмета, ни слова, ни учреждения, для которых записи не дают повода считать, что они существуют.

- **Насколько ему позволено додумывать — ползунок на три положения, рядом с рассказчиком.** «Нельзя» (по умолчанию): записи и есть весь мир, и там, где они кончаются, ролик так и говорит — этого никто не знает, — и ничего своего в дыру не кладёт. «По ситуации»: придумывает, но как починку, а не как лицензию, — только там, где текущий бит и правда иначе не написать, и только самое мелкое бытовое, что его расшивает: привычку, цену, порядок действий. Одной фразой, мимоходом, и дальше по ролику на это ничего не опирается. «В любом случае»: записи — лишь то, что кто-то записал, а мир дообставляется свободно, в той же фактуре. Тонкий лор в первом положении не пишется вовсе; мир, который ты всерьёз архивируешь, третьим положением портится; большинству миров нужно среднее — потому это и ползунок, а не галочка, какой оно было. **Ограничения во всех трёх положениях одни и те же**, потому что они никогда не были про то, СКОЛЬКО придумано: нельзя противоречить записи, нельзя вылезать за то, из чего этот мир сделан, нельзя придумывать **предмет** ролика (место, обычай, человека, ради которых видео и снимается, берут из записей — всегда), нельзя **чеканить имя собственное** (назвать вещь — самое сильное утверждение, какое рассказчик может о ней сделать; чеканку слышно как собственное слово мира, и отличить её потом уже невозможно) и нельзя класть подробности рядом с открытым вопросом — это читается как намёк на ответ. Модель, которой просто сказали, что придумывать *можно*, не дописывает цену фонарщика: она придумывает то, про что ролик, или даёт имя тому, что записи намеренно оставили открытым. Ровно этим версия на два положения и занималась. **И твой бриф тоже ничего не создаёт**: он решает, О ЧЁМ говорят, и не властен вызвать вещь к существованию, — поэтому на бриф, называющий то, чего в записях нет, отвечают, найдя, на что он показывает, и рассказав про ЭТО местным словом, либо прямо и изнутри мира сказав, что такого здесь не знают. Спроси горную почтовую станцию про «Объект» — и получишь место под девятой вешкой, названное как оно называется, а не чеканку.

`--scenario` здесь отвечает не на тот вопрос, что в драме: не *что происходит*, а **о чём именно из этого мира рассказать** — обычай, место, человек, необъяснённый случай — или **какую теорию по его записям отстоять**. Это две противоположные задачи, и сценаристу так и сказано: если просят теорию, её надо действительно построить — выложить свидетельства, назвать то, что не сходится, и дойти до вывода, а не пересказать улики и сойти на нет. Это утверждение, сделанное внутри мира тем, кто в нём живёт, а не фанатская теория о тексте.

- **Папка и есть мир** (`configs/fandoms/<имя>/`): `fandom.toml`, один или несколько `.md` с лором и `characters/*.toml` — собственные персонажи мира, лежащие здесь, а не в общей библиотеке, потому что они — часть мира: прогон в нём получает их, даже если ты не назвал ни одного, и они не засоряют список, из которого выбирают остальные режимы. Фандом — **директория**, в отличие от всех прочих видов конфигов, и имя папки и есть его тождество, что бы ни было написано в TOML. Сам TOML необязателен: папка с одним только markdown — уже валидный фандом со всеми настройками по умолчанию. Его ключи: **`docs`** — документы в порядке чтения (пусто = все `*.md` из папки по алфавиту; имя, указывающее в никуда, просто пропускается, а не роняет весь мир), **`tone`** — свободная заметка о тоне и манере, уходит сценаристу как есть, **`lore_tool`** — можно ли сценаристу запрашивать полный лор (см. ниже). **`canon`** и **`docs_sha`** пишет машина — это собранная справка и контрольная сумма того, из чего её собрали; руками их не трогают. В `configs/fandoms/example/` лежит небольшой мир (почтовая станция на перевале, её обычаи, её хроника и пять человек) — как образец. Готовой `canon` в нём намеренно нет: сгенерированный результат, положенный в репозиторий, отрывается от кода, который его порождает (эта справка уже успела — в ней остался заголовок раздела, переименованный с тех пор в компиляторе), а сборка — разовая цена за мир, которая дальше берётся из кэша; всё остальное в `configs/fandoms/` в `.gitignore`, как и прочие личные конфиги.
- **Персонаж здесь — это ВНЕШНОСТЬ, а не человек.** Драма свой каст выдумывает, поэтому персонаж там — это кто-то: имя, возраст, характер, который драма вольна написать. У мира люди уже есть, и записать о них нужно ровно то единственное, чего проза не может передать генератору картинок, — как они выглядят. Поэтому персонаж в фандоме — это лицо, плащ, корпус, силуэт, и **больше ничего**: ни поля возраста, ни поля характера вообще. Кто он, что сделал, какой он и что о нём думают — идёт в **лор-документы**, прозой, туда же, где погода и книги учёта: там это и читает сценарист, и там ты сам это найдёшь через полгода. Или не идёт никуда — это тоже нормально: у многого, что мир показывает в кадре, никакого характера и нет. Отсюда два следствия. Персонаж не обязан быть **человеком** — существо, машина, судно, здание, к которому история возвращается, — и человеческая форма нигде по пути не предполагается: ни в карточке визарда, ни в ИИ, который дозаполняет персонажа по лору, ни в компиляторе, превращающем `appearance` в промпт для генератора: у того, что без лица, он опишет форму, материал, размер и отметины. И персонаж не обязан быть **один**. У каждой записи есть пометка — единственное структурное поле кроме имени и внешности: **конкретный персонаж** (одно и то же лицо в каждом кадре), **много одинаковых безликих** (стражи, почтальоны — одна внешность на всех, имени ни у кого) или **целый вид** (раса, каста, модель машины, описанная тем, что общего у любого представителя). Пометка отрабатывает дважды: без неё группа читается сценаристом как отдельный человек и потихоньку обзаводится именем, репликой и личной аркой, — а скомпилированный промпт, который подставляется вместо имени в каждом кадре, обязан быть одной фигурой в *единственном* числе и без единой личной приметы, иначе «трое стражей» отрисуются как трое мужчин с одинаковым шрамом. Напиши в кадре «трое ⟨имя⟩» — в кадре будет трое; само имя при этом не меняется.
- **Канон-справка**: лор **один раз** компилируется нейронкой в плотную справку — что это за мир, как здесь говорят, правила, которые история нарушать не имеет права, табу, глоссарий, люди, места, группы, хронология, — и кладётся в `fandom.toml` как `canon`. Это ровно то же, чем `visual_prompt` приходится полю `appearance` персонажа, и по той же причине: мир нужен сценаристу в виде, который можно держать перед глазами в каждом из ~18 окон сценария, а восемнадцать чтений твоей сырой прозы стоят восемнадцати чтений и вдобавок тратят внимание модели на повествование, которое ей не нужно. Справка — **опись, а не пересказ**: каждое имя, число и дата в написании самих записей, по строке на запись. Единственный раздел, который не выписывается, а выводится, — **табу**: в мире троп и фонарей нет автомобилей и телефонов, и именно этот список не даёт рассказчику обставлять твой мир вещами из нашего. Свежесть определяется **контрольной суммой документов**, а не флажком «изменено», — именно потому, что лор удобно писать в своём markdown-редакторе, где никакой слопген не смотрит и никакой флажок не поднимется; пересборку запускает то, что текст на диске больше не сходится с `docs_sha`, так что переименование, перестановка и удаление обесценивают справку ровно как правка. Сохранение лора в TUI пересобирает её сразу же (сначала пишется файл, потом собирается справка — чтобы неудачный запрос к нейронке стоил свежести справки, но никогда твоего текста), а этап `canon` в начале прогона остаётся ленивым сторожем, который в обычном случае не делает ни одного запроса. Пересобранная справка пишется обратно в `fandom.toml`, так что следующему прогону — и TUI — она достаётся бесплатно; и она же едет вместе с задачей, поэтому продолженный прогон дописывает сценарий по тому миру, каким он был на старте (перепланировать половину сценария по изменённому лору — значит войти в противоречие с уже написанной половиной). Меньше **4000 символов** лора (`SMALL_LORE_CHARS`) — не компилируется ничего: документы короче, чем была бы справка, поэтому сценарист получает их целиком. Две вещи с одним ИМЕНЕМ — две записи, и компилятору это сказано прямо: если у двух сторон есть одноимённая должность, служба или обычай — ловчие, которые здесь работают так, а за межой иначе, — склейка их в одну строку надевает на одну сторону практику другой, причём как факт, который ниже по конвейеру уже не опознать. В контрольную сумму заложена **версия компилятора** рядом с документами, поэтому улучшение этого промпта списывает все уже лежащие на диске справки, а не доходит только до миров, чей лор потом правили; следующий прогон пересоберёт их по одному запросу, а TUI до тех пор помечает их устаревшими.
- **Почему три слоя.** Мир доходит до сценариста трижды, от дешёвого к дорогому, потому что поодиночке не работает ни один. **(1)** Канон-справка, в каждом окне, — опись, чтобы сценарист знал, что вещь *существует*, даже когда ему бы и в голову не пришло о ней спросить. **(2)** **Проход-план**, который читает весь лор одним запросом и раздаёт каждому отрезку видео те конкретные факты, которые этот отрезок обязан потратить: имена, даты, числа, места, обычаи, цитаты — в формулировках самих записей. Это единственный проход, читающий всё, поэтому деталь, которую он не раздал, — деталь, которую видео потеряло; отсюда и указание быть щедрым и класть каждую деталь ровно в один отрезок и никуда больше. Именно этим каналом на страницу попадает настоящая фактура мира. **(3)** `lore_lookup` — архивариус: нейронка-библиотекарь, которая читает весь документ ради ответа на один вопрос, и предлагается сценаристу настоящим function call'ом на ту деталь, о нехватке которой он *знает*, — прежде чем он назовёт имя или дату. Один третий слой провалился бы дважды: сценарист никогда не спросит про валюту, о существовании которой не подозревает (классический провал поиска — его лечит первый слой тем, что он опись), а каждый вызов перечитывает весь документ, так что ~3 вопроса на ~18 окон обходятся дороже, чем вставить лор во все 18. Отсюда и последняя очередь, а не единственный канал, — и отключение (`lore_tool = false` либо автоматически, когда лор настолько мал, что вставляется целиком), когда в записях нет ничего сверх справки. Архивариус тоже говорит изнутри мира: если записи молчат, он называет это молчанием *мира*, а не «автор не уточнял», — иначе он вернул бы сценаристу ровно ту рамку, ради избавления от которой режим и сделан.
- **Хребет, и зачем он короткому ролику.** Три слоя ставят перед сценаристом *мир* и ни один из них не говорит, что такое *ролик*. Замер, тридцатисекундный прогон с брифом «Первый день на Объекте»: три бита, каждый честный, в словах самого мира, построчно неулучшаемый — проходная и форма, потом вводная бумага, потом совет про галку. Три верных вещи про один предмет, и ни одна не следует из другой, — это и есть «прыгает с темы на тему». Ни одно правило режима нарушено не было, потому что ни одно правило режима не было про ролик. Проход-план — единственное планирование здесь — работает от четырнадцати битов и ниже не запускается вовсе, так что у каждого короткого видео плана не было никакого, а вместо него ему предлагали драмовскую арку (крючок → подъём → поворот → расплата) для куска, в котором нет сюжета; `FIDELITY_RULE` же, прочитав бриф из четырёх слов, объявлял его наброском и выдавал сценаристу вольную на все три бита. Вольная, без плана, без формы: три захода на тему — единственное, что могло получиться. Поэтому теперь записано, что такое кусок этого рода. Это ОДНА вещь, открытая с середины самой себя, разобранная в порядке, где каждая фраза вызвана предыдущей, повёрнутая один раз ближе к концу — когда выясняется, что у заведённого порядка есть цена, — и остановленная строкой, которая ничего не объясняет. Это `PIECE_RULES`: пять правил, сформулированных как проверки, которые сценарист действительно может применить, а не как вкус, который он применить не может (*вынь бит и вставь в другое видео про этот мир: если никто не заметил — он был не отсюда*; *поменяй местами два бита: если ничего не сломалось — ты написал список*). А для каждого ролика форму решает **проход-хребет**: один запрос до первого бита, который читает записи целиком против брифа и отвечает предметом в словах мира, одной из четырёх форм (**механизм** — один заведённый порядок, разобранный по частям, и чего он стоит тем, с кем это делают; **обязанности** — человека куда-то определили, и вот что это для него значит; **правило** — при каких условиях оно держится и что доберётся до тебя, когда не удержится; **виньетка** — один названный кто-то хочет одну конкретную вещь, и всё кончается недоделанным), фактом, с середины которого кусок открывается, тремя-шестью шагами по порядку, каждый следующий из предыдущего, поворотом и тем, что делает последняя строка. Сценарист получает это как свой порядок — и предупреждение, что шаги суть порядок куска, а не его биты. Заодно проход возвращает коротким роликам слой, которого у них никогда не было: ниже четырнадцати битов это первое, что читает записи целиком. Бриф длиннее 600 символов хребта не получает: оператор уже написал кусок, а написанное режут, а не перепланируют. Непригодный ответ хребта не фатален — кусок пишется по одним пяти правилам.
- **Заметка `tone` связывает в обе стороны, а у сухости есть причина.** `tone` в `fandom.toml` — та единственная фраза, которой ты описываешь, как звучит место, — доезжала до сценариста сноской под канон-справкой, и получалось, что доезжает та её половина, которая согласна с бюджетом длины, а вторая нет. Мир, просивший голос ровный, канцелярский, короткими фразами, и тут же — *охотно вставляет присказки и приметы*, получал ровность и ни одной присказки: ровно ту сухость, которую заметка просила, и ничего из того, чего она просила. Теперь у заметки свой заголовок, она объявлена связывающей в обе стороны, доходит и до хребта, и до каждого окна, и стоит выше бюджета длины — бит, который не влезает, теряет факт, шаг или деловую оговорку, но не регистр, потому что бит, ободранный до информации, — это доска объявлений. Рядом с этим каждый бит обязан нести одну вещь сверх своего факта: что делают руки или глаза слушателя, чего он ждёт или что уже тратит в уме, меру в собственных единицах мира, присказку, которую здесь повторяют, или одну физическую подробность, которую можно было и не называть, — и какая из них по вкусу твоему миру, решает твоя заметка о тоне, а вот будет ли хоть одна — уже не она решает. И ничто не может быть *всегда* или *как правило* там, где может быть *в этот раз*: общая истина о предмете («первый день всегда одинаков» в куске про твой первый день) — самая сухая фраза из доступных, и она ставит слушателя снаружи того самого, внутрь чего кусок его и заводит.
- **План и кнопка, которая переписывает по нему сценарий.** Фандомный ролик решается в шести полях до того, как написан первый бит, — про что он, какой формы, с чего открывается, шаги по порядку, поворот, чем останавливается, — и брейкпоинт `script` теперь открывается ими, над битами, которые из них вышли. Правишь любое поле и жмёшь **переписать по плану**: прогон остаётся припаркованным, биты пишутся заново по тому, что ты только что написал, а документ возвращается с новым сценарием под тем же планом. Это дешёвый круг. Дорогой — править шесть закадровых строк, приводя их к плану, который ты хотел бы иметь; в конце получается кусок, подогнанный под нужный вид, а не вышедший из плана, с которым ты согласен. Поле формы — выпадающий список твоего каталога форм, так что «здесь должен был быть выбор, а не механизм» — это одно поле и одна кнопка. Полностью очищенный план считается снятым; план, вписанный руками в прогон, у которого его не было, работает ровно так же.
- **Два рассказчика**, выбираются на прогон (`--narrator` или шаг «Мир» в визарде), и оба — изнутри мира; разница в том, где именно они в нём стоят. **`resident`** там живёт: первое лицо, мир как быт, что он видел, что здесь знают все и чего здесь никто не может объяснить, чужие реплики — сырыми и без «сказал такой-то». Это голос драмы, наведённый на мир вместо сюжета. **`chronicler`** его изучает: архивариус, исследователь, помешанный, перечитавший слишком много книг учёта, — он говорит о записях своего мира так, как историк говорит о наших: сухо, конкретно, со ссылками, датами и теми местами, что не сходятся. «Я» он себе позволяет — но про собственные рассуждения, героем он не является. Обоим запрещено открываться определением и обращаться к аудитории, для которой мир в новинку: слушают такие же местные, и не хватает им не азов, а того, что ты нашёл в записях. То же и с первым битом: он начинается с конкретного момента, предмета или утверждения, уже посередине, и никогда с фразы, которую пишут только для того, кто здесь ни разу не был.
- **Откуда берутся кадры** — один вопрос вместо цепочки. Четвёртый шаг визарда у драмы — это *оркестрация*: упорядоченный список генераторов, каждый со своей долей видео и своей политикой ротации ключей. Эта механика есть не просто так — полнометражная дорама выжирает бесплатные дневные лимиты где-то на середине и вынуждена прыгать по сервисам прямо на ходу, — а видео по фандому это одна вещь на несколько минут, которой не нужны ни сама механика, ни рамка, которую она накидывает на работу. Поэтому этот режим спрашивает единственное, что здесь действительно важно, — **откуда берутся кадры**: **🤖 ИИ-генерация** (рядом выбор генератора), **🙋 генерирую сам** или **🔍 ищу сам**. Одноэтапную цепочку, по которой пойдёт конвейер, режим собирает сам: дальше по конвейеру всё говорит цепочками, и это единственное место, где более простой вопрос надо перевести. Два «операторских» ответа останавливают прогон на этапе видеоряда и выдают список кадров (`slopgen gather`): промпты для вставки, если генерируешь, брифы плюс готовые запросы, если ищешь, — см. [материал от оператора](#материал-от-оператора-либо-генерируешь-сам-либо-ищешь-сам).
- **Всё остальное — драмовское**, без изменений: каст со скомпилированными визуальными промптами и общим бюджетом внешности, реестр визуала (`entities`), фиксирующий всё повторяющееся, что не человек, вся битовая механика — проход-план, окна, подгонка голоса и картинки, `--clip-s`, решающий, сколько сюжета влезает в бит, — ИИ-генерация клипов и оба user-assisted пути (делать клипы руками или идти их искать), реклама, `--visual-notes`, `--visual-style`, `-F/--filter`, `--clean-subs` и все брейкпоинты, плюс один свой. Чего он **не** берёт — так это серий: сериал режут там, где больнее всего, а у рассказа о мире нет клиффхэнгера, на который можно повесить обрыв, поэтому здесь нет ни `--parts`, ни поля частей, ни этапа `cut`. И редактора оркестрации он не берёт — по причине прямо выше. И каст он берёт не по-драмовски — см. выше. Учти только, что генератор о твоём мире не слышал: в промпте кадра ни одно его слово не может остаться непереведённым, поэтому сценарист описывает, как вещь *выглядит*, обычным английским («не the winter carry, а figures in heavy coats carrying mail sacks single file along a snowbound mountain path»), а озвучка продолжает называть её местным словом. Цепочка этапов: `canon → script → entities → tts → picture → footage → subtitles → assemble → metadata` (`picture` планирует дорожку из базы кадров и пропускается прогоном, у которого кадры делает генератор), и `canon` — такой же брейкпоинт, как остальные: последний бесплатный момент починить мир, пока против него не написано ни строчки.

Запуск из TUI (Генерация → Фандом) или headless: `slopgen fandom ru example --scenario "…" --narrator chronicler --duration-min 3 --tol 20 --parts 2 --orchestration my_chain`.

## Переписка (`configs/chat/`, `configs/personas/`)

Четвёртый режим и первый, у которого картинку slopgen **рисует сам**. Переписку — групповой чат, ветку комментариев, что-то придуманное из головы — читают вслух и рисуют интерфейсом мессенджера, сообщение за сообщением, так, как оно приходило бы на телефон. Интерфейса три: **телеграм** (светлый чат на обоях, облачка по сторонам, хвостик у угла), **дискорд** (тёмный плоский список: аватарка, имя, строки), **реддит** (белая ветка, отступы у ответов, строка кармы под каждым комментарием).

- **Реддит не смешивается с двумя остальными.** Телеграмный экспорт, нарисованный дискордом, — нормальное желание, и при этом ничего не теряется. Реддит не взаимозаменяем ни с тем, ни с другим: его единица — не реплика, а пост с **деревом** комментариев, он делает отступ вместо стороны и считает карму там, где мессенджеры ставят реакции. Сплющи ветку в чат — и половина ответов перестанет на что-либо отвечать; отправь чат в обратную сторону — и у каждого облачка появится счёт, которого никогда не было. Поэтому режим отказывает в такой комбинации, а не выдаёт правдоподобное враньё.
- **Кто читает — три ответа.** Карточка каждого участника (`configs/personas/*.toml` несёт ник, картинку, цвет и голос), **один голос на всех** (частый случай: один диктор читает чужой групповой чат — формат, ради которого режим и сделан, — и перекарточивать восьмерых ради этого значило бы потратить вечер на настройки) или **никто**. Последнее — формат, а не поломка: переписка, которую читают с экрана под музыку, — это видео. Сообщение, которое никто не читает, появляется целиком и висит ровно паузу.
- **Сообщение приходит так, как его читают.** Длинное появляется кусками — по `chunk` символов, каждый кусок встаёт на первом своём *слове*, по таймингам, восстановленным озвучкой, — так что облачко растёт под диктора, а не висит готовым, пока он догоняет. Короче `chunk_min` символов сообщение появляется целиком: двухсловная реплика, показанная в два приёма, читается как заикание. `gap_s` — фиксированная пауза между сообщениями, она же вся длина сообщения, которое никто не читает вслух.
- **Когда экран кончился** — три ответа, и третий доступен ещё и первым двум. **Еду следом** — лента едет вверх за новым сообщением, длиной `roll_s`, как в настоящем клиенте. **Перескок** — встаёт на новое место за кадр. **Чищу экран** — экран пустеет, и переписка начинается сверху. Очистку можно пометить на любом отдельном сообщении: лента, которая едет всё видео, всё равно хочет чистого экрана там, где решил *ты*, а где это — зависит от того, что говорят.
- **Реакции появляются, когда сообщение дочитано**, одна за другой и тем быстрее, чем их больше: `react_s` — это вся пачка целиком, сколько бы их ни было, так что восемь реакций не занимают в восемь раз больше одной.
- **Переводит себя сам и знает, когда не надо.** Найденная переписка написана на том языке, на котором её вели. Трудность не в переводе: русский чат, где пишут `ну всё, я go sleep`, обязан сохранить `go sleep`, потому что иноязычность **и есть** шутка, — а переводческий API, которому дали этот кусок, честно его переведёт, потому что ему дают куски, а суждение требует всей переписки. Поэтому это идёт в уже настроенную у тебя модель, которая видит все сообщения и возвращает только те, которым перевод действительно был нужен. Оригинал остаётся рядом с переводом: проверять перевод без оригинала — не проверять ничего.
- **Шрифты — собственные у каждого клиента**, запрашиваются у fontconfig по имени и никогда по пути: конфиг с путём работает на той машине, где его написали, а на NixOS — до ближайшего обновления, которое его подвинет. Настоящие шрифты телеграма и дискорда нераспространяемы, поэтому ставится открытый аналог, который просит каждый скин (Roboto, Inter, IBM Plex Sans), плюс Noto Color Emoji; машина без них нарисует переписку тем гротеском, который у неё есть, и ролик будет чуть не тот, а не отсутствующий.
- **Аватарки, обои и звуки отправки — обычные папки**: `assets/avatars/`, `assets/chat_bg/`, `assets/chat_sfx/`, как `assets/music/`. У картинки нет свойств, которые надо записывать, так что TOML на каждый файл был бы обслуживанием в обмен ни на что. Безкартиночных не оставляют пустыми: им рисуют кружок с инициалами, как делает любой настоящий клиент, цветом, выведенным из имени, — а это ровно то, что даёт экспорт, потому что аватарок в экспорте нет вовсе.
- **Сколько переписок в одном ролике** — это настройка (`want`), потому что ролик из трёх чатов — это формат, а не краевой случай: источнику надо знать, сколько идти искать, а комната просто держит столько, сколько ты в неё положил. У каждой своё название на шапке, а свайп между ними рисуется, а не режется.
- **9:16 или 16:9.** Кадр принадлежит прогону, а не режиму, поэтому поворот поворачивает всё: и рисование, и субтитры, и доставку. На широком кадре переписка кладётся в центральную колонку с обоями по бокам, а буквы сохраняют тот размер, под который нарисованы: масштаб по ширине умещал бы на экран три сообщения — такого чата никто не видел.
- **Разделённый экран** — для тех, кому нужно движение во второй половине: чат сверху, петля снизу (файл в `assets/footage/`, папка или жребий). Петля не начинается заново, а перематывается туда, где стоит кусок, так что геймплей едет через каждое сообщение; `split_change_s` вместо этого меняет клип раз в столько-то секунд.
- **Звук отправки** из `assets/chat_sfx/`, по разу на приходящее сообщение — не на каждый кусок длинного и не ещё раз на реакцию, потому что телефон так не делает.
- **Настройки — сохраняемый набор** (`configs/chat/*.toml`): их три десятка, и набор, из которого получается «реддитовская история», отличается от набора для «куска группового чата» почти в каждом поле. Будучи обычным именованным конфигом, он сохраняется, переименовывается, перезаписывается и удаляется в панели конфигурации, как всё остальное. `configs/chat/example.toml` и `configs/personas/example.toml` лежат как образцы.

**Ролик делается в комнате.** Нажми **🎬 собрать вручную** в форме запуска — или **🗨 переписка** на припаркованном прогоне — и перед тобой сама вещь, в три колонки. **Переписки**, из которых состоит ролик: их добавляют, называют, переставляют, разрезают надвое и склеивают обратно; **сообщения** той, что открыта, у каждого — отправитель, то, на что оно отвечает, реакции, время на облачке, ник и картинка на одно это сообщение и пометка «чистить экран здесь»; и настоящий **кадр** того, как это будет выглядеть, нарисованный тем же рендерером, что будет рисовать ролик. **Вставить текстом** принимает блок строк «Ник: текст» разом — так переписка, скопированная откуда-то, и приходит; всё, что на такую строку не похоже, продолжает сообщение выше, так что реплика, написанная в три строки, остаётся одной репликой. **Кто в переписке** показывает участников, у кого сколько реплик и кто их читает, и одной кнопкой заводит любому карточку — ник, картинку и голос, которые переживут этот ролик, а не осядут на одном задании.

**Настройки меняются с кадром перед глазами.** В комнате есть лист со скином, шапкой, таймингами и мелочами; правка применяется со следующей отрисовки. Настройки принадлежат ЭТОМУ ролику: как только тронешь любую, он перестаёт следовать набору, из которого его запустили, — иначе правка здесь меняла бы и все остальные ролики на том же наборе.

**И есть часы, когда им есть откуда взяться.** Как только переписки разложены и нарисованы, под кадром появляется полоса, где отмечено каждое сообщение, а свайпы — ярче. Тащи по ней — и в кадре то, что показывает готовый ролик на этой секунде: то состояние, которое в этот момент стоит, обрезанное там, куда к этому моменту доехало окно, посчитанное той же арифметикой, которой рисует ffmpeg. До этого таймингов не существует вовсе, и полоса не появляется, вместо того чтобы их выдумывать.

Цепочка — ряд кнопок сверху, и жмут их в том порядке, в каком идёт работа, а не в каком написан список: написал, нарисовал, посмотрел, поправил реплику, нарисовал снова.

Ответ указывает на сообщение номером внутри своей переписки, поэтому удаление и перестановка тащат все указатели за собой, — а ответ на удалённое сообщение становится ответом в никуда и никогда ответом на то, что въехало в освободившийся слот. Правка сама по себе ничего не переозвучивает: таймлайн появляется, когда жмёшь **сценарий**, а озвучка переиспользует все дубли тех реплик, которых ты не трогал.

Цепочка стадий — `source → script → tts → render → subtitles → assemble → metadata`. `cut` нет: переписка — одно видео, а отдельные куски внутри неё — вещь на экране, а не отдельная публикация. `render` стоит после `tts`, потому что «когда появляется кусок длинного сообщения» — это вопрос о таймингах слов, а их нет, пока слова никто не произнёс. Брейкпоинт есть на каждой стадии, и важный из них — `source`: там прогон встаёт с перепиской перед глазами, пока ты решаешь, что это за ролик.

Запускается из браузера (Генерация → переписка). Поля длины там нет намеренно: ролик идёт ровно столько, сколько идёт переписка.

## Монтажная

Единственный экран в браузере, который не форма, — верстак для одного **фандомного**
видео, чьи картинки берутся из базы кадров мира.

**Как войти.** Заполни фандомную форму и нажми **🎬 собрать вручную** вместо ЗАПУСТИТЬ:
прогон заводится — папка, чекпойнт, настройки, — и *ничего не запускается*. Монтажная
открывается на пустом видео. Запустить цепочку и остановить её на брейкпоинте так не
получится, поэтому это вторая кнопка, а не галочка: к моменту, когда появился бы экран,
прогон уже решил бы ровно то, ради чего его открывают. Две другие двери — для прогона,
который уже идёт: брейкпоинт `picture` и кнопка **🎬 монтажная** в строке любого
запаркованного прогона с озвученными строками. **← назад** выходит; прогон лежит в
списке обычным запаркованным прогоном, с той же кнопкой, и она возвращает ровно туда,
где ты был. В вкладке ничего не держится: каждая правка лежит в чекпойнте ещё до того,
как экран перерисовался, — закрыть браузер посреди монтажа не стоит ничего.

**Как вернуться, когда ролик уже смонтирован.** Готовое видео — не тупик. В его строке
стоит **🎬 перемонтировать**, и нажатие снимает с работы *рендер* — дорожку картинки в
том виде, в каком она разложена по сценам, файл субтитров каждой части и сам
смонтированный кусок — и паркует видео, а именно это и делает его снова
правкопригодным: все правящие маршруты монтажной отказывают уже смонтированному видео,
потому что иначе файл на диске остался бы ответом, пока дорожка под ним говорит другое.
Больше не снимается ничего: строки, дубли, карточки, движения, эффекты и вид — это ровно
то, ради чего ты вернулся. И ничего не удаляется: `сборка` пишет каждую часть по тому же
пути, что и раньше, так что старый монтаж перепишется новым, когда тот будет собран, а до
тех пор его можно смотреть — за этим в готовое видео и возвращаются. Кнопка просмотра в
монтажной так и говорит — **посмотреть прошлый монтаж**, — пока играет именно он.
**Кем он был смонтирован, неважно**, и случай «его делала нейронка» тут основной: что
подборщик поставил под строку не ту карточку, обычно видно только в готовом ролике —
то есть ровно тогда, когда монтажная раньше была уже закрыта. Недоделанными остаются три
стадии — видеоряд, субтитры и сборка, все три нарисованы на рейке непройденными, — и
любой способ закончить перепишет файл на месте: нажать их здесь либо нажать **собрать и
продолжить** и отдать это цепочке. Видеоряд в этом списке по той причине, по которой его
отсутствие незаметно: именно он раскладывает кадры по сценам, а сборка рендерит то, что
он там оставил, — не снять эти куски значит перемонтировать дорожку впустую. Там, где
картинка берётся из базы кадров, это один проход по дорожке и ни одного вызова модели. Уйти не дожав — прогон останется честно недоделанным и будет так
выглядеть, а не готовым прогоном, чей файл спорит с собственной дорожкой. Заголовок и
публикация не трогаются намеренно: перемонтаж — это новый *файл*, а не новая публикация,
поэтому описание не переписывается, а уже ушедшая серия не уходит второй раз.

**Конвейер вразбивку.** Сверху — каждая стадия цепочки кнопкой: канон мира, сценарий,
реестр вещей, озвучка, дорожка картинки, видеоряд, субтитры, сборка, описание. Нажал —
прогналась: тот же самый вызов, который сделал бы оркестратор, над той же работой и с
той же записью в списке пройденного, — поэтому прогон, который вели руками, потом
подхватывается цепочкой и проходит мимо уже сделанного. Стадия, которой нечего дать,
не прячется, а гаснет: видеть, чего не хватает, полезнее. Отчитывается она туда же,
куда отчитывается всякая стадия, — в лог и полосу самого прогона, потому что озвучить
сорок строк это минута, в которую этому экрану сказать нечего. Порядок — твой: работа
на самом деле идёт «написал — послушал — посмотрел — переписал вот эту строку —
переозвучил её — нарезал — назначил — нарезал ещё», и ничто из этого не порядок списка.
Жать **сценарий** никто не заставляет: набери строки сам и жми **озвучку**.

Каждое нажатие **сначала спрашивает**, и вопрос тут и есть смысл: ряд из девяти
одинаковых фишек ничего не говорит о том, какая из них зовёт нейронку, какая бесплатна,
а какая заменит строки, которые ты набирал час. Поэтому в окне — имя стадии, одна строка
о том, что она делает, и список того, что она потратит и что перепишет: *«ВЫБРОСИТ и
напишет заново все строки, включая набранные руками: 6»*, — а кнопка красная там, где
это именно так. Числа считаются по работе, так что они про это видео, а не
предупреждение вообще.

Сделанное руками **засчитывается за ту стадию, которая это делала бы**. Набрал сценарий
здесь — `сценарий` помечается пройденным, потому что его результат лежит в работе, как
бы он туда ни попал; то же с озвучкой, нарезкой и назначением. Это не бухгалтерия: ровно
это не даёт кнопке **дальше** в строке прогона уйти в писателя и выбросить все набранные
строки — что она и делала, пока список пройденного просто переносился, а не вычитывался
из работы. И наоборот, `дальше` становится полезной: сделай руками ту половину, которая
тебе важна, а остальное пусть дособерёт цепочка.

А под всем этим — две собственные дорожки видео, наложенные друг на друга.

Дорожка картинки в этом режиме **нарочно** не совпадает с речью: она режется там, где
рассказчик переводит дух, и именно эта рассинхронность и делает из папки со
стоп-кадрами смонтированный ролик, а не слайд-шоу. Разбирать её сорока строчками вида
`4.1–8.3с · наезд · рынок` — это разбирать фильм по монтажному листу, а две вещи,
которые с ней на самом деле хочется делать, в строчку вообще не вписываются. Поэтому:

- **Стык — это слово.** Кликни слово, с которого должен начинаться кадр, — и он с него
  начнётся, а тот, что шёл до, в этот же момент кончится: стык это и есть. На видео, где
  дорожки ещё нет вовсе, первый такой клик её заодно и закладывает: пустая дорожка — это
  не отсутствие, а один неразрезанный кадр на каждый кусок, ждущий, когда его разрежут,
  и единственный жест, вокруг которого построен экран, не должен требовать сначала нажать
  что-то ещё. Второй путь — **нарезать по речи**, разом и там, где рассказчик переводит
  дух; обе кнопки лежат в пустой полосе кадров — там, где стоит человек, не понимающий,
  как сделать кадр. **✕** на
  левом краю кадра убирает стык, и кадр сливается с предыдущим. Хранится при этом
  слово, а не секунда (`FrameShot.anchor_word` лежит там с тех пор, как дорожку
  написали), поэтому поставленный руками стык переживает всё, что двигает часы:
  переозвучь строку вдвое медленнее — и каждый стык после неё уедет вместе со словом, на
  котором стоял, а не окажется за полсекунды от него.
- **Стык перетаскивают, и он липнет к словам.** Возьми блок кадра и тяни его по дорожке:
  он встанет не туда, где остановился курсор, а на начало слова — с одного на следующее,
  — и от блока вниз к этому слову побежит пунктир, а само слово подсветится. Свободное
  перетаскивание было бы вторым, худшим ответом на вопрос, на который этот экран уже
  ответил, — и отвечало бы *секундами*, то есть той единицей, которая не переживает
  переозвучку. Перетаскивание — одна операция, а не «убрать стык и поставить заново»,
  потому что в два нажатия кадр между ними теряется: ✕ сливает его с соседним, и карточка,
  движение камеры и закреп уходят вместе с ним. Слова, на которые стык встать не может,
  просто не предлагаются, поэтому тянуть некуда, где ответят красной строкой: слово, на
  котором уже начинается другой кадр (два кадра на одном мгновении — это один кадр),
  первое слово куска (перед ним стоит тот самый начальный кадр) и всё, что внутри рекламы.
  У кадра, которым кусок начинается, стыка нет вовсе — он и курсор-ладонь не берёт.
- **Ролик всегда начинается кадром.** Первый кадр дорожки — не чей-то стык: он стоит на
  00:00.000 и означает просто «здесь начинается видео», поэтому убрать его с дорожки
  нельзя — **✕** на нём снимает с него *картинку* и оставляет сам кадр стоять пустым над
  своими секундами. Раньше его можно было убрать: отличали его только по началу, а кадр,
  нарезанный по первому СЛОВУ, начинается на пару десятых позже — рассказчик сначала
  вдыхает. Уберёшь такой — и дорожка начинается через три секунды после начала ролика:
  первыми секундами не владеет никто, рендер молча отдаёт их соседней картинке, а экран
  этого уже не чинит, потому что единственный его жест — разрезать кадр, который идёт, а
  над теми секундами не идёт никакой. Теперь начальный кадр возвращается всюду, где
  пропал, — в том числе в прогонах, сохранённых до этой правки, — и то же верно для
  куска после рекламы: каждый кусок начинается своим кадром. Клик по первому слову куска
  отвечает этим самым кадром, а не осколком в четверть секунды перед ним.
- **Звук и текст — одна дорожка и две правки.** **Текст** строки — это то, что читают:
  вшитые субтитры собираются из её таймингов слов, и сохранение текста раскладывает
  новые слова по тому же промежутку, не трогая ни звук, ни часы. **Голос** строки — это
  то, что слышно, и именно из него сделаны часы, поэтому переозвучка (на скорости
  прогона или на своей — ползунок рядом с кнопкой) меняет её длину и сдвигает всё, что
  после. Можно и прочитать строку самому и принести запись: тайминги снимет
  распознаватель — та же дорога, которой идёт `--tts-source manual` для целого видео.
  Строки здесь ещё и **добавляют и выбрасывают** — в отличие от дорамы, и стоит сказать
  почему. Там бит *и есть* клип: добавил — значит, появился кадр, который надо
  сгенерировать, и пересинхронизировать всё, что после. Здесь дорожка картинки про биты
  не знает вовсе: она режется по словам и нарочно едет мимо речи, — поэтому вставленная
  строка стоит ровно одного: кадр, попавший на шов, вырастет на её длину, и его режут,
  если так не надо. Все остальные кадры сохраняют карточку, все стыки — своё слово, и
  ничто уже озвученное заново не озвучивается. Новая строка появляется немой и не
  занимает времени, пока её не озвучишь, — и до тех пор лежит в полосе **без голоса**
  под дорожкой, потому что часам такую строку рисовать негде: десяток их делит одну
  секунду и схлопывается в огрызок, по которому не попасть. Текстовое поле держит
  черновик, который переживает перерисовку, каретку и уход на другую строку и обратно;
  **уход из поля его сохраняет**, и всё, что двигает строки под ним, — ＋, озвучка,
  запуск стадии — тоже: черновик хранится по номеру строки, а терять из-за сдвига
  номеров то, что человек набрал, — так себе размен.
- **Строки переставляют перетаскиванием** — за шапку блока: ниже неё сплошные слова, а
  слово — это жест разреза, поэтому ручкой служит та единственная полоска, которая больше
  ни за что не отвечает. Блок едет за рукой, а жирная черта показывает шов, на который он
  встанет; он именно прилипает к швам, потому что строки — это последовательность, и
  бросок может значить только одно: между какими двумя. Долгое время этого не было — на том
  основании, что переставленный сценарий это дорожка, которую набирают заново, — и это было
  неверным прочтением собственного устройства комнаты: стык это СЛОВО, и эффект тоже, а
  слова едут вместе со строкой, в которой стоят. Привязки перенумеровываются вместе с
  перестановкой, и каждый стык выходит из неё на том же слоге, на котором его поставили, с
  той же карточкой, которую под эти слова выбрали, и со стрелкой, по-прежнему указывающей
  на то, на что указывала. Заново не набирается ничего. Чего перестановка действительно
  стоит — это **длины картинок по обе стороны шва**: кадр идёт до следующего стыка, а
  перестановка строки двигает стыки друг мимо друга, поэтому кадр, у которого сосед только
  что уехал в конец ролика, дорастает до того, что теперь перед ним. Это честная
  арифметика — секунды обязаны кому-то принадлежать, — и именно на неё стоит посмотреть
  после. Дубли при этом переименовываются по новым местам, и это та половина, которая иначе
  портит звук: `tts` называет дубль по номеру строки, и оставленная без присмотра
  перестановка оставляет все эти имена указывающими на чужой голос.
- **Пауза — это вещь на дорожке.** **＋ пауза** ставит кусок тишины после выбранной
  строки (а если ничего не выбрано — в конец), и вся она целиком — это длина: ни текста,
  ни голоса, ничего внутри к ней не прибито. Рисуется она на полосе строк, штриховкой,
  потому что она *в* этой последовательности: стоит между двумя строками так же, как
  стоит строка, и всё, что после, отъезжает на её длину. Тянут её за **правый край** —
  единственное, что у неё есть; число в блоке растёт за рукой, а остальное видео
  подтягивается на отпускании. Именно за правый: двинуть левый значило бы «начать тишину
  раньше», а это длина строки перед ней. **Две подряд не делаются**, и кнопка при этом
  гаснет, а не ждёт, чтобы отказать: две паузы вплотную — это одна пауза, сказанная
  дважды (с любой из них нельзя сделать ничего, чего нельзя сделать с одной вдвое длиннее),
  а на дорожке с такими двумя попытка растянуть первую выглядит как кнопка, которая
  ничего не делает, — потому что число, которое ты читаешь, стоит на второй. Выброси
  строку *между* двумя паузами — и они срастутся, сложив длины: правило восстанавливается
  там, где его можно нарушить, а не только там, где его задают. Рендер пауза не держит:
  тишина — это то, для чего она есть, она собирается как те секунды, которые занимает, и
  звуковая дорожка превью ровно на столько же длиннее. Во всех списках брейкпоинтов она
  живёт строкой только на чтение — эти списки пересобирают сценарий из того, что им
  показали, поэтому пауза без строки была бы паузой, которую невозможно сохранить.
- **Выбор кадра — это разглядывание картинок.** Выбери кадр, и вся база мира ляжет
  перед тобой миниатюрами, с числом размеченных областей на каждой. Клик — и карточка
  встаёт на кадр и **прибивается**: следующий проход подборщика волен планировать вокруг
  твоего выбора, но не поверх него. Клик по той же карточке перекатывает движение камеры
  заново. Перетащи картинку на кадр — она станет новой карточкой мира и тут же встанет
  на место, а разметку предложат нарисовать, пока ты на неё смотришь.
- **Движение камеры** — отдельный блок, и отвечать на него можно двумя способами,
  переключателем, **по умолчанию простым**. *Простое* — это четыре вопроса: РОД (держим,
  лёгкий наезд, медленный проезд, наезд, отъезд, панорама), ОБЛАСТЬ, на которую
  сходиться, из размеченных на карточке, и два, которые планировщик до сих пор кидал
  жребием и никому не показывал: когда проезд трогается и сколько едет, в секундах от
  начала кадра, с читалкой *«стоит 1.4 · едет 1.8 · стоит 1.4с»*. Шесть названных
  пресетов закрывают большинство кадров — поэтому они и остаются умолчанием.
  *По ключам* — для остального: список моментов, каждый из которых КОГДА, КУДА смотреть
  и НАСКОЛЬКО близко; камера идёт от ключа к ключу по прямой, до первого и после
  последнего стоит. Два ключа — это обычный проезд, то есть ровно то, чем пресет и
  является: поэтому переход на ключи засевает список из того пресета, на который ты
  смотрел, а не с чистого листа. Выбор области в ключе ставит окно **ровно туда, где
  оно размечено**, — и центр, и рамку, — а ползунок близости работает уже вокруг
  этого; раньше брался только центр, рамка оставалась во всю картинку, а окно такого
  размера может стоять лишь по центру, и зажим утаскивал его обратно в середину.
  Целишься в стол — получаешь комнату. То же теперь и для пресета, выставленного
  руками: порог качества, который держит автоподборщик подальше от глубоких кропов
  мелких карточек, к прицеленному руками кадру не применяется — поднять его окно до
  порога значит не смягчить движение, а сбить прицел. Три и больше — то, чего пресетом не скажешь: подержать
  на комнате, наехать на стол, увести к двери. Каждый момент ещё и **флажок на
  дорожке**: висит под линейкой, остриём вниз, в свой кадр, — тащишь его вбок, чтобы
  подвинуть, а нажатие на флажок или на строку списка подсвечивает оба и ставит
  плейхед на это мгновение: это один момент, показанный дважды. Пресет при этом лежит под ключами
  нетронутым, так что выбор обратим в обе стороны и ни одна ничего не стоит. Правка
  тайминга не перекатывает рамки — обеими ручками можно пользоваться вместе. Движение,
  которого карточка не умеет (панорама требует двух размеченных областей), оставит всё
  как было, а не выдумает, и скажет об этом; на кадре без картинки блок пригашен и
  объясняет почему — движение строится по геометрии карточки. Карточке-**клипу**
  достаются те же ручки и то же движение: раньше ей отказывали на том основании, что у
  неё своё движение и два движения поверх одной картинки дерутся, — хорошее умолчание и
  плохое правило, потому что половина клипов, которые набирает мир, это статичные планы
  комнаты, а для остальных есть «держим». У клипов теперь есть и **постер** — вынутый
  из них кадр, с кешем, — чтобы карточка-видео показывалась в полосе лицом, а не пустой
  плиткой со значком: выбирать картинку по имени этот режим как раз и не велит.
  **▶ проиграть кадр** играет один этот кадр и останавливается на его дальнем краю: по
  стоп-кадру движение не увидишь.
- **Почему движение могло никуда не ехать.** Окно кропа не может показывать меньше
  исходных пикселей, чем есть на выходе, иначе картинку не кадрируют, а растягивают, —
  правило справедливое, но без дна: карточке не шире видео кроп запрещался **целиком**,
  и тогда любой наезд, отъезд и панорама, какие `move_for` умеет построить, выходили с
  одинаковыми концами и рисовались стоп-кадром. Нигде об этом не говорилось. Правило
  по-прежнему работает там, где карточка достаточно крупная, — купленная вдвое больше
  видео всё так же режется вдвое, и именно это покупает глубокий заход на размеченную
  область, — но перестало запрещать движение вовсе: слегка мягкое движение это худшая
  картинка, а замороженное — сломанная функция. Мелкая карточка пишет в блоке
  **«карточка мелкая — кроп её слегка мылит»**, а у дорожки, чьи движения посчитаны по
  старому правилу, появляется кнопка **«оживить движения»** — только пока есть что
  оживлять: она пересобирает каждое движение из уже выбранных карточки, рода, области и
  тайминга, так что здоровая дорожка пересобирается один в один, а сорок мёртвых кадров
  не надо перещёлкивать руками.
- **Предпросмотр и правда рядом с ним.** Канва играет дорожку от одного звукового файла,
  собранного из всех строк подряд, — чтобы часы между ними не разъезжались: карточка, её
  вписывание в кадр, движение кропа на этой секунде, субтитр в синхроне и набросок
  монтажных фильтров. **Ползунки фильтров тут же**, и набросок едет за ними, пока тянешь.
  Набросок — нарочно: зерно, разъехавшийся цвет трубки и порванную строку считает ffmpeg,
  а канва умеет их только изображать. Поэтому **точный кадр** рендерит кадр под
  плейхедом настоящей цепочкой, в полный размер, — положить поверх наброска и щёлкать
  туда-сюда.
- **Ничего здесь ни у какой нейронки не спрашивают.** Все решения на этом экране твои и
  делаются тыканьем в слово или в картинку, а **собрать и продолжить** закрывает
  брейкпоинт *без* перезапуска стадии — перезапуск ровно и означал бы отдать час ручного
  монтажа обратно подборщику, которого этот час переспоривал. Кнопка отказывается, пока
  что-то не соберётся (строка без голоса, кадр без картинки), и говорит, сколько того и
  другого, — вместо того чтобы ffmpeg сказал это через двадцать минут, не назвав ни
  того ни другого.

**Настройки** открывают поверх монтажной настройки самого прогона — потому что каждая
стадия цепочки тут кнопка наверху, и каждая стадия их читает. Иначе получается комната,
которая жмёт **озвучку**, но не может выбрать голос, и жмёт **описание**, пока тумблер
«сочинять ли его вообще» лежит на форме, с которой прогон ушёл час назад. Группы —
ровно те стадии, которые ещё можно нажать:

- **голос** — движок, клонированный голос, скорость речи и **начитаю сам**
  (`tts_source`): озвучка становится такой же ручной работой, как самодельный видеоряд
  — стадия выписывает текст и ждёт wav-файлы, а тайминги слов снимает с них
  распознаватель. Отдельную строку можно начитать и без этого, кнопка в блоке строки
  была всегда; это — когда начитывать будешь все.
- **музыка** — какой трек из `assets/music/` играет под голосом; единственная ручка в
  этом листе, которую здесь **слышно**: предпросмотр крутит его под голосом по кругу и
  на той же громкости, с какой его подмешает `assemble` (`[audio] music_volume`).
  Пусто — и это по умолчанию — значит не «без музыки», а «какой выпадет»: жребий
  посеян на самом прогоне, поэтому в монтажной играет ровно тот трек, который окажется
  в готовом ролике, и предпросмотр не врёт. ПАПКА (всё, что кончается на `/`) — тот же
  жребий, но по узкой полке: так у режима заводится своя музыка, а жребий остаётся.
  `none` — тишина. Выбор, который с тех пор переименовали, перенесли или опустошили, —
  тоже не ошибка: прогон скажет об этом в лог и бросит жребий заново, потому что
  переехавший файл должен стоить выбора, а не музыки.
- **эффекты** (`frame_effects`) — вешает ли нейронка сама заготовленные стрелки, обводы
  и звуки карточек на слова. Поставленное руками не трогается в любом случае; про всю
  эту машинерию — свой раздел ниже.
- **субтитры** — стиль, которым пишется `.ass`, и вычищать ли мат из вожжённого текста,
  пока голос произносит всё как есть.
- **что на выходе** — `write_metadata`, аккаунт для публикации, «не публиковать» и
  оставлять ли временные файлы.

Это настройки ПРОГОНА, а не открытого видео — один чекпойнт несёт один набор параметров
на всю пачку, — и действуют они *вперёд*: стадия возьмёт их, когда ты её нажмёшь, а уже
сделанное останется как сделано, потому что переозвучить строку в новом голосе и значит
переозвучить её. Каждая ручка сохраняется сама, как и всё здесь, и ложится в чекпойнт
раньше, чем перерисуется лист, — так что возобновление потом прочитает то, что ты
поставил. Значение, которого в списке нет (движок, который не установлен; аккаунт,
который удалили), отвергается со списком того, что есть, а ручка возвращается к тому,
что в прогоне на самом деле, вместо того чтобы оставить на экране враньё. Чего в листе
нарочно нет — сценарной половины (бриф, мир, насколько можно придумывать): **сценарий**
при нажатии выбрасывает все строки, и комната, весь смысл которой в строках, набранных
руками, — неподходящее место, чтобы это выглядело заманчиво.

**Монтирую сам** (`frame_by_hand`) — переключатель, и живёт он в самой монтажной, рядом
со стадиями: он решает, что делает одна из них. Включён — **дорожка картинки** не
спрашивает нейронку, какая карточка к какому куску подходит: режет дорожку по речи,
оставляет кадры пустыми, ничего ни у кого не просит и отдаёт назначение тебе. Выключен —
та же кнопка сперва спрашивает подборщика, и ты споришь с тем, что он решил; это обычный
способ пользоваться монтажной. Галочка есть и на форме, где она вдобавок добавляет
обычному прогону брейкпоинт `picture` (проскочивший его унёс бы пустую дорожку в стадию
видеоряда), — но настройка, меняющая смысл здешней кнопки, должна лежать там же, где
кнопка.

## База эффектов (`configs/effects/`)

База кадров говорит, **что на экране**. База эффектов — **что поверх него срабатывает**:
стрелка на предмет, который называют, обвод вокруг него, звук под фразой. Это
единственный слой режима, который нарочно **синхронен** речи: дорожка картинки идёт
мимо narration'а специально (именно эта рассинхронность и делает из стопки стопкадров
смонтированный ролик), а стрелка, приехавшая на полсекунды позже своего слова, — не
опоздавшая стрелка, а неправильная. Поэтому эффект вешается на **слово**, ровно как
склейка, и по той же причине переживает переозвучку: секунды едут, слово — нет.

- **База одна на все миры**, в отличие от кадров, и это следует из природы вещей.
  Карточка — картинка *откуда-то*, она принадлежит своему миру; стрелка везде стрелка, а
  базу, которую надо копировать в каждый новый мир, скопируют неправильно. К миру эффект
  привязывает карточка, которая вешает его на свою область, — и живёт эта привязка на
  карточке, там же, где координаты.
- **Эффект — это картинка, клип, звук или картинка со звуком.** `configs/effects/
  <имя>.toml`, материал лежит рядом — как картинка рядом с карточкой и сэмпл рядом с
  клонированным голосом. Один звук — полноценный эффект, который ничего не рисует; одна
  картинка — молчаливый. Кинь файл в **конфигурация → эффекты**, и он станет эффектом;
  вторую половину приносят уже в редакторе.
- **`description` — то, чем эффект вообще достижим для нейронки.** Одна строка про то,
  *когда он уместен*: «когда в тексте называют предмет, который видно на картинке». Её и
  читает проход эффектов. Неописанный эффект прекрасно ставится руками и просто никогда
  не будет выбран сам — это честный исход: угадать, что значит безымянный png, модель не
  может.
- **Три привязки, и первая едет вместе с картинкой.** `point` ставит эффект на саму
  картинку — точкой в любом её месте или рамкой, обведённой вокруг чего угодно, — а
  кроп-движение это окно по той же картинке: обвод вокруг шапки сходится с наездом ровно
  так же, как сама шапка. Путь по кадру считается из движения арифметикой и отдаётся
  ffmpeg как путь, а не рисуется в экранных координатах, чтобы потом сползти с предмета.
  `screen` ставит эффект в одно из девяти мест кадра, что бы за ним ни было: вспышка,
  рамка, звук — всё, у чего вообще нет картинки. `full` кроет весь кадр — засветка,
  плёночный глитч, материал, который не *в* картинке, а *поверх* неё, поэтому он
  масштабируется до покрытия, а не ставится куда-то. Эта же разница решает, *откуда
  эффект может взяться*: точечный — это координаты на одном файле, и предложить его может
  только карточка этого файла; остальные два ничего не требуют от того, что на экране, и
  предлагаются на любом куске.
- **Точка, а не размеченная область.** Область кропа существует, чтобы в неё *смотреть*:
  это окно, в которое может въехать камера, то есть рамка определённого размера вокруг
  определённого предмета. Требовать её, чтобы ткнуть стрелкой, значило, что база умеет
  показывать только на то, во что кто-то уже решил наехать, — а половина того, на что
  ролику надо показать, вообще не предмет: угол, просвет в очереди, пустая половина
  стола. Поэтому размещение не отчитывается ни перед чем: ставь точку где угодно или
  обводи рамкой что имел в виду, и перетаскивай, когда не туда.
- **`width` — доля от ширины кадра** (0.35 — треть экрана), и она задаёт размер там, где
  его не задаёт ничего другое: у экранного эффекта и у поставленного одной точкой. Дай
  **две** точки — и они задают и место, и размер, а `width` молчит: обведённая рамка *и
  есть* рамка, потому что множитель поверх прямого перетаскивания — это второй орган
  управления на одно решение. Под `full` она тоже молчит.
- **Анимация — набор моментов**, той же формы, что и расставленное руками
  кроп-движение: когда, насколько крупно, насколько смещён (в долях собственного размера И в
  собственных осях — поэтому кончик стрелки держится на предмете, как бы близко ни
  подъехала камера и куда бы её ни нацелили: повёрнутая влево подлетает справа),
  насколько повёрнут, насколько виден. Между моментами всё интерполируется, до первого и после последнего —
  держится. Пять заготовок заполняют список одним нажатием (**всплыть**, **пружинка**,
  **мигает**, **уплывает** и «без анимации») и остаются отправной точкой, а не типом:
  сохраняются моменты, поэтому «пружинку на десятую медленнее» правят числом, а не
  заявкой на фичу.
- **Редактор — это холст и полоса**, потому что делаешь ты ровно две вещи: двигаешь
  предмет и решаешь, когда он это делает. Холст — формы самого видео, с эффектом на нём:
  **тяни его** — правишь смещение выбранного момента, тяни за угол — его крупность, за
  ручку сверху — поворот; а
  точечный эффект показан поверх квадрата-заменителя, то есть того, относительно чего на
  настоящей карточке меряется его размер. Под холстом — все моменты метками на
  собственных часах эффекта: нажатием по полосе двигаешь голову воспроизведения, меткой
  — сам момент во времени, **＋ момент** добавляет его там, где стоит голова (и берёт
  значения, которые анимация в этот миг и так имеет, — то есть добавление момента ничего
  не меняет, оно лишь даёт, что менять). Числа никуда не делись — если ты знаешь, что
  хочешь 0.15 с, набери 0.15, — они просто перестали быть единственным входом, из-за чего
  первая версия этого экрана была таблицей про картинку, которую никто не видел.
- **И повёрнутым.** У каждого момента есть `rotate` в градусах: одна картинка стрелки
  показывает во все стороны сразу, и восемь стрелок базе не нужны. Он анимируется, как
  и всё остальное: стрелка может влететь, разворачиваясь, печать — лечь криво. На
  холсте у поворота своя ручка над предметом (тяни по кругу, с **Shift** — по
  пятнадцать градусов), и крутится он вокруг центра — единственной оси, которая
  переживает изменение размера. В рендере это фильтр `rotate` с углом-выражением;
  холст, на котором он вертится, растёт до диагонали картинки, чтобы не срезать углы, а
  следующий за ним scale получает эту прибавку обратно — поэтому повёрнутый эффект
  выходит того размера, который просили, а не того же, делённого на √2.

- **Повёрнутый — и поворачивающийся.** У момента есть угол, как есть размер, поэтому
  одна картинка стрелки показывает во все стороны сразу и базе не нужно восьми стрелок.
  Два момента с разными углами — это поворот на лету: стрелка, которая приезжает
  доворачиваясь, печать, которая ложится криво. В ffmpeg часы называются `t` в `rotate`
  и `T` в `geq`, а рендер возвращает повороту его поля обратно — чтобы повёрнутая
  картинка была того размера, который просили, а не размера собственной диагонали.
- **Нацеливают там, где пользуются, и три угла складываются.** АНИМАЦИЯ поворачивает
  (`EffectKey.rotate`, ручка на холсте самого эффекта); КАРТОЧКА целит — куда стрелка
  показывает на *этой* картинке: ручка на призраке в редакторе карточки или поле
  **поворот°** рядом; и одно СРАБАТЫВАНИЕ целит ещё раз — куда она показывает над *этой*
  строкой: **Shift**-перетаскивание по превью в монтажной или поле **поворот°** в
  инспекторе. Они **складываются**, а не перекрывают друг друга — ради этого их и три:
  стрелка, нацеленная на дверь, всё так же доворачивается по своей анимации, а
  подправленная для одного ролика не выбрасывает прицел, которым пользуются все
  остальные. С **Shift** угол везде липнет к пятнадцати градусам: целятся обычно в
  прямой угол или в диагональ. Ручка в редакторе самого эффекта поворачивает **все
  моменты** сразу на одну и ту же величину, и это не срезанный угол, а разница между
  «нацелить» и «анимировать»: повернуть один момент, оставив остальные в нуле, — не
  значит навести стрелку, значит заставить её раскрутиться до угла и обратно, что и
  читается как сломанная анимация. Поворот на лету делается нарочно — с **Alt** (только
  этот момент) или числом в поле **поворот°** у самого момента.
- **Сдвинуть по ВРЕМЕНИ — свободно, и этим эффект отличается от стыка.** Возьми блок
  срабатывания на полосе (или внутри картинки, которой он принадлежит) и тяни по дорожке.
  Он встанет туда, куда бросил: стык — это слово, потому что картинка меняется на слоге
  или меняется неправильно, а акцент нацелен на речь, а не прибит к ней — он опережает
  слово на пятую долю секунды, он закрывает вдох между двумя, он идёт поперёк трёх, — и
  привязка к началам слов сделала бы половину видимых глазу положений недостижимой.
  *Хранится* при этом всё равно привязка, и это та часть, которая не очевидна: секунда,
  куда бросили, записывается как ближайшее слово плюс остаток, поэтому переозвучка уносит
  срабатывание вместе с речью, на которую его нацелили, а не оставляет его на секунде,
  которая теперь принадлежит другой строке. Перетаскивание прибивает его, как и всё,
  поставленное руками. Срабатывание, которое несёт сама КАРТИНКА, останавливается на
  границах своего кадра — его координаты это факт про одну карточку, и рендер кончает его
  там, где меняется кадр, так что тяга за стык не сдвинула бы его, а удалила, — а то, что
  ты сам уронил из базы, перевешивается на ту картинку, куда попало, и своё место теряет:
  те координаты мерили по картинке, которой под ним больше нет.
- **Сдвинуть в ПРОСТРАНСТВЕ для одного ролика — прямо на превью.** Выбери срабатывание в монтажной, и
  превью берёт курсор: тащи — эффект окажется там, куда бросил. Точка считается обратно
  через кроп-окно, которое стоит в этот момент, поэтому хранится координата на
  **карточке** и эффект по-прежнему едет вместе с картинкой. Лежит она при этом на
  **метке**, а не на карточке: во всех остальных роликах стрелка показывает туда же, куда
  и показывала, а «вернуть как на карточке» отменяет сдвиг. Рамка при переносе держит
  форму; эффект, которому места не ставили, становится точкой там, куда его принесли.
- **Точный кадр рисует их тоже.** Кнопка `точный кадр` гонит один кадр через настоящий
  ffmpeg, и это та кнопка, которая отвечает на вопрос «что там будет на самом деле», —
  поэтому она ставит и то, что срабатывает в этот момент: с тем размером, прозрачностью
  и углом, до которых доехала анимация, и в том же месте цепочки, где их ставит финальный
  проход.

- **Повторяющаяся середина: вход, цикл, выход.** Часы анимации режутся **двумя
  разделителями** на *вход*, повторяющуюся *середину* и *выход* — и тогда одно
  срабатывание это вход, середина сколько-то раз и выход. Оба разделителя таскаются по
  той же полосе, где лежат моменты: ключ на 0.4с относится ко входу или к циклу
  в зависимости от того, где стоит разделитель, и просить оператора держать два числа
  в согласии об этом — значит просить его считать то, что редактор может просто
  показать. Те же три части есть числами (**вход**, **цикл**, **выход**) — когда у тебя
  на руках именно число, — и **повторов** задаёт счёт по умолчанию.
  **Длину такому эффекту не набирают**: три пульса *и есть* три пульса длиной, поэтому
  орган управления — счётчик, а секунды — показание. Метка на таймлайне может сказать
  свой счёт (здесь три, над следующей строкой шесть), и в монтажной у таких меток
  вместо длительности показаны **повторы**, а рядом арифметика (`× 0.65с = 1.95с`).
  В рендере при этом не меняется ничего: повтор — это складка на часах анимации,
  поэтому снятый путь выходит той же формы, что и всегда, и ffmpeg ни о чём новом не
  узнаёт.

- **Клип либо зацикливается, либо сам кончает эффект.** `hold` — держим столько,
  сколько сказано, и зацикливаем то, что короче: искра, пульсирующее кольцо, материал без
  собственного конца. `clip` — конец клипа и есть конец эффекта: взрыв, который
  отыгрывает, печать, которая ставится. Про `.webm` стоит знать: это единственный формат,
  доносящий альфу через видеокодек, а стрелка с чёрными углами — не стрелка.
- **Карточка вешает эффект на картинку — перетаскиванием.** В редакторе карточки, под
  областями, список того, что эта картинка **умеет**: какой эффект, одна строка, что его
  вызов значит **здесь** («обвести шапку»), и три размещения — **точкой**, **рамкой** или
  **в кадре** (то есть не на картинке вовсе). Нажми — и эффект появится на холсте ровно
  таким, каким будет выглядеть, с точкой, показывающей, к чему он привязан; тащи его на
  предмет, тяни за угол — размер, за ручку сверху — поворот всего эффекта. По этой строке и выбирает нейронка,
  поэтому «эффект 3» не годится, а «обвести шапку» — да. Призрак рисуется в том моменте,
  куда анимация **приехала**, со всеми смещениями, а не в точке привязки: иначе стрелку,
  которая висит НАД предметом, рисовало бы прямо на нём — и каждую стрелку в базе
  ставили бы на одну свою длину выше, делая ровно то, что показала картинка.
- **Кто их вызывает.** После того как подобраны картинки — и только после, потому что
  лишь тогда ясно, какая карточка стоит на каком куске и, значит, что этот кусок умеет, —
  делается один вызов модели на всё видео. Ей дают каждый кусок, картинку на нём, что эта
  картинка умеет, и слова, пронумерованные сквозняком; она отвечает номером слова на
  каждый эффект. Чего она **не** решает — ритм: два эффекта ближе двух секунд нельзя,
  один и тот же дважды за двенадцать секунд нельзя, и потолок — восемь в минуту на всю
  дорожку. Смысл и ритм — разные суждения, и ни одно не годится на работу другого; ровно
  тот же раздел, что у базы кадров между подборщиком и ритмом.
- **Там, где ничего не заготовлено, это ничего не стоит.** Пустая база, мир, чьи
  карточки ничего не вешают, или снятая галочка — и вызова просто нет. **Эффекты
  расставит нейронка** (`frame_effects`) есть на форме фандома и в настройках монтажной;
  выключенная, она отменяет только **выбор**: поставленное руками она не трогает и
  ритмом не срезает.
- **В монтажной** у эффектов своя дорожка — между стопкадрами и словами, то есть там же,
  где они и в видео: нарисованы на картинке, поставлены на слово. Нажми пустую дорожку
  над словом — предложат то, что кладётся поверх. Нажми блок — выберешь эффект,
  поставишь, сколько он висит, или снимешь. Превью рисует их поверх кадра по тому же
  пути, который считает рендер, а не по второй реализации единственной по-настоящему
  трудной арифметики в этой фиче. Эффект, чей файл ушёл из базы, всё равно нарисован на
  дорожке, помечен и всё ещё твой, чтобы его снять; а тот, которому на карточке не
  поставили место, так и говорит — он будет висеть в кадре, а не на предмете.
- **Две двери, потому что это два разных действия.** То, что несёт КАРТОЧКА, —
  нацеленное на предмет, тот самый список, из которого стреляет автоматический проход, —
  вызывается от картинки: выбери кадр и нажми **＋ эффект кадра**, он встанет на слово
  под головой воспроизведения. Всё остальное, что у тебя есть, вызывается с полосы под
  кадрами: нажми её над словом — предложат остальную базу, которая падает в центр кадра
  и тащится куда надо. Каждое меню одной строкой говорит, где второе, так что ничего не
  спрятано; в каждой строке видно сам эффект и сказано, что произойдёт («встанет на свою
  точку на картинке», «упадёт в центр кадра — потом перетащи»).
- **И таймлайн эту разницу рисует, а не объясняет.** Блок кадра **растягивается вниз**, и
  его собственные срабатывания лежат внутри него: это то, что делает эта картинка, они
  обрезаются её границами, и склейка или переозвучка, укоротившая кадр, укорачивает их.
  Всё, что положено поверх руками, лежит на своей полосе ниже, вне блоков. Где два
  однородных пересекаются по времени, тот, что позже, уходит на следующую строку, а
  полоса (или блок) вырастает на строку — так что сверху всегда тот, кто начинается
  раньше, и ничто ничем не перекрыто.
- **Сдвинуть в одном ролике.** Эффект размещают один раз, на карточке, и там он стоит
  во всех роликах, которые его вызывают. Иногда одной строке хочется его на полладони
  левее — поэтому выбранное срабатывание в монтажной можно просто **потащить по
  превью**. Точка, куда его бросили, пересчитывается назад через кроп-окно, которое
  стояло в тот момент, так что записывается место на КАРТОЧКЕ и эффект по-прежнему едет
  вместе с картинкой; записывается оно на МЕТКУ, так что карточка продолжает говорить
  своё для всех остальных. В инспекторе видно, что срабатывание сдвинуто, и есть
  «вернуть как на карточке». Рамка при перетаскивании держит форму: все точки едут на
  одно и то же, поэтому обвод по дороге не меняет размер.
- **Точный кадр рисует их.** «Точный кадр» отдаёт один кадр через настоящую цепочку — и
  теперь ставит на него то, что в этот момент срабатывает: в одном мгновении все
  рампы схлопываются в числа, и эффект становится обычным оверлеем. Кнопка,
  существующая ради ответа на вопрос «что там будет на самом деле», не имеет права
  умалчивать про слой, который ты только что поставил.

- **Куда они ложатся в рендере.** В финальном проходе — после плёночных фильтров и до
  субтитров: стрелку положено читать, поэтому она не уходит под зерно, а титры остаются
  поверх всего, потому что титры — это то, что видео говорит. Звуки подмешиваются в том
  же проходе, с задержкой до момента срабатывания, своей громкостью и без
  перенормировки голоса — чтобы озвучка не становилась тише от того, что в ролике
  случилось много звяков.

## Профили видеоряда (`configs/visuals/`)

Видеоряд — слоёная композиция, настраивается профилями:

- **Фон**: `stock_video` / `stock_photo` / `local_video` / `local_photo` / `ai_photo` / `ai_video` (бесплатная генерация без ключей — картинки Pollinations, видео Wan через HF Spaces). Привязка `narration` — нейронка выдаёт запрос картинки/футажа на каждые ~N секунд речи, привязанный к тому, что произносится в этот момент (Швейцария → столица, пара → пара, щенок → щенок); `neutral` — случайный/зацикленный контент (например геймплей). Фото-фон получает движение Ken Burns (`none`/`subtle`/`strong`) и меняется каждые `interval_s` секунд.
- **Передний план**: опциональные вставки-картинки/клипы в рамке — *по событию, а не по таймеру*: нейронка сама решает, какие произносимые фразы заслуживают иллюстрации, и каждая вставка показывается ровно пока звучит её фраза (тайминг из пословной разметки edge-tts) и исчезает после. Ты задаёшь только источник, ширину и позицию.
- **Кто поставляет материал** (`manual = true`, на любом из слоёв): это флаг, а не отдельный источник — *откуда берётся материал* и *какого он рода* суть два независимых вопроса, и вмешаться ты можешь в любое из семейств. `stock_video`/`stock_photo` + `manual` — это **поиск**: слопген говорит, что нужно на каждый кадр, и выдаёт слова, которыми это ищется, а файл находишь ты. `ai_video`/`ai_photo` + `manual` — это **генерация**: слопген пишет промпт, а клип ты делаешь во внешнем веб-сервисе. `local_*` флаг игнорирует — эти файлы и так лежат на диске. `ai_model` игнорируется везде, где выставлен `manual`: называть больше нечего. Эта пара настроек и заменила старую запись `ai_model = "manual"`, которая умела выразить только половину — генерацию.

Готовые профили: `classic` (сток-видео, дефолт), `slideshow` (фото в такт тексту с Ken Burns), `gameplay` (кинь клипы майнкрафт-паркура/сабвей-сёрфа в `assets/footage/gameplay/` — поверх будут выскакивать картинки по тексту), `ai_slideshow` (картинки Pollinations без ключа, в такт тексту), `ai_broll` (сгенерированный клип на сцену — бесплатно, медленно), `ai_manual` (каждый клип генерируешь руками ты) и `search` (каждый кадр находишь ты). **Профиль разбирается на ручки в обеих мордах.** Шаг «Видеоряд» в TUI и карточка «Откуда
картинка» в браузере держат одни и те же контролы — источник фона, кто его поставляет,
генератор, привязку, папку, темп смены и движение фото, «один клип насквозь», а также
источник, ширину и позицию вставок — и обе предзаполняются ИЗ выбранного профиля. Именно это
позволяет карточке из четырнадцати ручек не менять ничего: форма, которую никто не трогал,
воспроизводит профиль в точности, поэтому прогон несёт ИМЯ профиля — то, что видно в списке
прогонов и что останется верным, когда профиль потом поправят. Сдвинешь одно поле — прогон
понесёт собственную копию. Поле, о котором карточка не спрашивает, сохраняет ответ профиля,
а не падает в дефолт модели, а `manual` обнуляет выбор генератора: у материала, который
поставляет оператор, называть нечего — поэтому выбрать `ai_manual` и ничего не тронуть не
считается правкой.

### Стиль графики: одно описание — во всех промптах

Поле на шаге **«Видеоряд»** во всех трёх мастерах (`--visual-style`), где ты своими словами говоришь, как видео должно ВЫГЛЯДЕТЬ, — «аниме» или три абзаца про зернистую 16-мм плёнку и натриевый свет фонарей. Оно **один раз за прогон** компилируется в тот диалект, на который генераторы картинок и видео действительно отзываются, — короткое английское перечисление тегов через запятую, не больше 16, — и дальше дописывается к **каждому промпту генерации в прогоне**: фон и передний план, клипы и неподвижные кадры, ролик-минутка, дорама и фандом одинаково, промпты, которые слопген отправляет сам, и промпты, которые ты руками вставляешь в Kling. Оно связывает только ВИД — ровно так же, как `--visual-notes` связывает то, что можно показывать: ни слова о том, что в кадре, потому что едет оно на промптах, где это уже сказано, и одно лишнее существительное попало бы во все сорок кадров.

Спрашивают его только там, где на него есть чем ответить: поле появляется, когда в прогоне что-то действительно генерируется — фон или вставка `ai_photo`/`ai_video`, либо цепочка с генератором (`manual` считается: кадр, сделанный руками, делается из того же промпта), — и не показывается для стока, локальных файлов и чистого поиска, которым некуда его вставить. Написать стиль и потом переключить источник обратно тоже не выйдет: чего мастер больше не показывает, того он больше и не спрашивает.

Компиляция нужна обоим краям того, что пишут люди. «Аниме» без обработки приходит в генератор одним иностранным токеном, и тот печатает его подписью поперёк кадра; даже написанное как «anime» — это слово, а не вид: вид держится на горстке тегов, за которые слово стоит (cel shading, плоский цвет, толстый чистый контур, рисованные фоны). У длинного описания беда обратная: это предложения, в них намешано и про сюжет, а дописанное к сорока промптам оно перевешивает сами кадры. Поэтому короткое разворачивается в то, что оно значит, длинное сваривается до того, что оно показывает, и на выходе оба одного размера и одной формы.

Стоит это один запрос к нейронке в первый раз, когда стиль применён, и ничего дальше: результат кешируется на диске по написанному тобой тексту, так что возобновления, следующие серии и следующие прогоны берут его бесплатно. Сорвавшийся запрос прогону ничего не стоит: в дело идут твои собственные слова, если ты писал по-английски, и ничего, если нет, — кадр без тегов стиля всё ещё кадр, а кадр с кириллицей поперёк испорчен. В режиме **поиска** теги никогда не попадают в запросы (сток индексирует то, что *на* картинке, а не то, как её нарисовали): туда стиль уходит как предпочтение для брифинга — учитывается там, где настоящий материал мог бы так выглядеть (чёрно-белое, архивное, неоновая ночь, снято на плёнку), и игнорируется там, где не мог.

### Фильтры: один вид на весь смонтированный ролик

Вторая половина того же желания, только на другом конце конвейера. Блок на шаге **«Видеоряд»** во всех трёх мастерах (`--filter ИМЯ[=ДОЗА]`, флаг повторяемый), который накладывает эффекты на **готовое, смонтированное видео** в ffmpeg: `bw` (ч/б), `film` (плёнка), `bloom` (свечение), `vignette` (виньетка), `grain` (зерно), `vhs` (кассета), `crt` (ЭЛТ), `glitch` (глитч).

Стиль графики *просят у генератора*, и каждый раз он отвечает по-своему, а у стокового клипа или папки со своими съёмками его не спросишь вовсе. Фильтр же применяется к тем кадрам, которые уже пришли. Поэтому он работает во всех режимах, с любым источником и выглядит одинаково, чем бы картинка ни была сделана; ему вообще не важно, есть ли в прогоне хоть один промпт.

Каждый эффект — **доза от 0 до 100**, а не выключатель: 20 — намёк, 100 — шутка целиком. Они складываются и складываются всегда в том порядке, в каком перечислены: цвет (`bw`, `film`), оптика (`bloom`, `vignette`), носитель (`grain`, `vhs`, `crt`), сигнал (`glitch`), — а не в том, в каком ты их включил, потому что зерно, добавленное перед размытием, — это размытое зерно, а этого никто не имел в виду.

Фильтр идёт **через всё видео, от первого кадра до последнего**, и он намеренно не покадровый: половина этих эффектов — рассказ о том, на чём это видео якобы играет (кассета, кинескоп, проектор), а кинескоп, который появился на один кадр и ушёл, — это не рассказ, а переход. Дораме, разрезанной на серии, тот же вид достаётся на каждую серию целиком.

Эффекты идут **только по картинке**. Субтитры вжигаются, а рекламный оверлей ставится *после* них и остаются чистыми: и то и другое существует, чтобы это читали (в том числе платформа), а мусор поверх подписи стоит разборчивости и не даёт ничего. Всё это происходит внутри того же финального прохода, который и так перекодировал видео, так что стоит доли одного прохода — ни лишней генерации, ни лишнего запроса, ни лишнего файла.

### Материал от оператора: либо генерируешь сам, либо ищешь сам

Оба вида `manual` останавливают прогон на этапе видеоряда в чистом состоянии `paused` (это не падение), выписывают список кадров и ждут тебя — `slopgen gather`, экран сбора в TUI или просто файлы, положенные в `manual/inbox/` прогона под именем `shot_NN.<расш>`. Генерация из этих двух старше и описывается проще: по промпту на кадр, вставляешь в Kling/Veo/Pika, приносишь файл обратно — бесплатные дневные лимиты обходятся прыжками по сервисам и аккаунтам, а это ровно то, чего машина за тебя сделать не может.

**Поиск** — та же конструкция вокруг другой задачи. Слопген не умеет посмотреть на клип и понять, годится тот или нет, поэтому он и не делает вид, что ищет по стокам, как человек; вместо этого он делает ту половину, в которой действительно хорош, — решает, что нужно каждому кадру, и пишет слова, которыми это вероятнее всего найдётся. Это именно перевод, а не копирование: промпт кадра и индекс стока — разные языки. «Марта склоняется над сортировочным столом, свет лампы ложится на сургучные печати, медленный наезд» не лежит ни в одной библиотеке; там лежит «woman sorting mail warm light» и «hands wax seal close up». Поэтому один проход нейронки переписывает каждый кадр дважды: **бриф** в одну фразу на языке озвучки — для того человека, который пойдёт искать (кадрирование, настроение и то, чего в кадре быть *не* должно: «без лиц», «без надписей», «съёмка сверху»), — и **3-5 коротких английских запросов** для самих сайтов, которые индексируют английский, на каком бы языке ни говорило видео. Запросы идут от самого перспективного к остальным и намеренно разные: буквальный, пошире, на настроение, — чтобы пустая выдача по первому не означала пустой кадр.

Тот же проход решает **фото или видео, по каждому кадру**, потому что этот выбор принадлежит материалу, а не настройке: сургучная печать крупным планом просит фотографию, мулы на тропе — движения, а тот, кого послали за *видео* печати, вернётся ни с чем. Это совет, а не правило: считается то, что ты реально принёс, вид манифест читает прямо по файлу, а неподвижный кадр просто держится и панорамируется (Ken Burns) на нужную биту длину.

Всё, что дальше, — механика, которая уже была у user-assisted генерации, переиспользованная, а не написанная заново: тот же манифест `manual/manual_shots.json` (главный — тот, что на диске; экран сбора лишь смотрит в него), те же зеркала `manual/prompts/<кадр>.txt` — где у поисковой задачи лежат бриф, строка `[photo, ~7.0s]` и запросы под ней, — тот же инбокс, куда теперь принимаются и картинки (`.jpg` `.jpeg` `.png` `.webp`), а не только видео, тот же `slopgen gather` и те же посерийные правила для дорамы. Брифинг идёт **пачками по 20 кадров на запрос**: сотня с лишним кадров в одном ответе возвращается с хвостом в духе «аналогично предыдущим»; а пачка, чей запрос сорвался, откатывается на обычное описание кадра, а не роняет прогон, — поиск получается хуже, но он остаётся поиском.

## Профили нейронок (`configs/llm/`)

Именованные подключения: `provider` (`deepseek`/`gemini`/`openrouter`/`custom`), `model`, `base_url`, `temperature`, `web_search`. Активный выбирается через `[llm].profile` в `slopgen.toml`. Ключи API никогда не лежат в TOML — только в `.env`; в TUI (Конфигурация → Профили нейронок) есть пресеты моделей по провайдеру, ввод ключа (сам сохранится в `.env`), тумблер веб-поиска, активация и удаление профилей.

**`temperature` ставится под прозу, а JSON переспрашивается холоднее.** Все стадии, кроме
одной, разговаривают с моделью в JSON, и примерно выше 1.2 модель перестаёт удерживать
конверт, в котором едет её же проза: вырождается посреди фразы и дописывает за
закрывающую кавычку — и совершенно годная идея возвращается неразбираемой. Замерено на
`deepseek-chat` при `temperature = 2.0`: 3 ответа из 10 были невалидным JSON на коротком
нишевом брифе и 8 из 10 на длинном, а это при трёх повторах — одна сорванная стадия из
тридцати семи для короткого и одна из двух для длинного; второе случается достаточно
часто, чтобы читаться как «генерация темы сломана». Поэтому первая попытка идёт на твоей
температуре, а попытка, вернувшаяся неразбираемой, переспрашивается с потолком 1.2, затем
0.7. Это всегда именно потолок: профиль на 0.7 не трогается, а жар снимается только с
модели, которая только что доказала, что на нём не умеет писать JSON, — так что до ролика
доезжает самый горячий ответ из тех, что разобрались. Ставь температуру какую хочешь —
намеренно тупой профиль это честный сценарий использования, — и знай, что пустышки оттуда
это модель, а не падение.

**Веб-поиск** (`web_search = true`): даёт модели настоящий инструмент `web_search` через стандартный function calling. Перед написанием сценария модель сама его вызывает, слопген выполняет бесключевой поиск DuckDuckGo и возвращает результаты — так озвучка опирается на реальные проверенные факты, а не на выдуманные имена/события. Работает на любом провайдере, чья модель поддерживает tool-use (OpenAI, DeepSeek, OpenRouter, compat-эндпоинт Gemini); модель без tool-calling просто не станет его использовать.

**Цены и счёт.** `price_in`, `price_cached` и `price_out` — сколько стоит модель в долларах за миллион токенов, по прайсу провайдера (`price_cached` — цена попадания в кеш промптов; оставишь 0 — попадание считается как обычный вход). Это конфиг, а не зашитая в слопген таблица: цены меняются, а устаревшая таблица тихо показывает неправильное число. С нулями прогон всё равно считает все токены — просто не переводит их в деньги. В любом случае весь счёт по ходу дела пишется в `checkpoint.json` прогона и читается так:

```bash
slopgen usage output/20260827_213735_fandom_ru          # токены по стадиям, повторы, кеш, стоимость
slopgen usage output/20260827_213735_fandom_ru --calls  # каждый запрос по отдельности
```

Смотреть в первую очередь на `RETRY` — повтор платит за весь контекст заново, — и на `CACHED`: сколько входа провайдер отдал из своего кеша промптов, а не прочитал.

**Своя модель под свою работу (`[llm.stage_profiles]`).** Запросы у слопгена неодинаковые. Писать сценарий по целому миру — работа для дорогой модели; скомпилировать внешность одного персонажа в теги, назвать готовый ролик, превратить кадр в запрос к стоку или переписать реплику с матом — поручение, и в фандомном прогоне поручений больше, чем письма. Каждый вызов и так знает свой вид, так что маршрутизация — это одна таблица в `slopgen.toml`:

```toml
[llm]
profile = "deepseek"          # всё, чего нет в таблице ниже

[llm.stage_profiles]
char_compile  = "дешёвая"     # внешность одного персонажа -> теги
style_compile = "дешёвая"     # стиль графики прогона -> английские теги
metadata      = "дешёвая"     # название/описание/теги готового ролика
lookup        = "дешёвая"     # кадр -> запросы для стоков
idea          = "дешёвая"     # выбрать тему инфо-ролика
```

Виды: `idea`, `length`, `script`, `drama_outline`, `drama_script`, `fandom_outline`, `fandom_script`, `fandom_canon`, `fandom_brief`, `lore_lookup`, `drama_entities`, `drama_shot_fix`, `char_compile`, `char_autofill`, `style_compile`, `metadata`, `profanity`, `censor`, `lookup`, `bp_rewrite`, `bp_scenes`, `vision`. Не указанный вид, несуществующий профиль и профиль без ключа одинаково откатываются к активному — неверно настроенная дешёвая модель не стоит тебе сценария.

Ключи стоков (Pexels, Pixabay) тоже можно вставить в TUI: **Конфигурация → Ключи API футажа** — они сохраняются в `.env`. Нужны только для `stock_*` видеоряда; локальным ассетам не требуются.

## Движки озвучки и клонирование (`[tts]`, `configs/voices/`, `slopgen models`)

Движков четыре, выбираются через `[tts].engine` (или `--tts-engine`, или TUI → Конфигурация → Движок озвучки):

| движок | русский | тайминги слов | скорость | цена |
| --- | --- | --- | --- | --- |
| `edge` **(по умолчанию)** | 2 голоса + 12 мультиязычных | **даром вместе со звуком** | быстро | бесплатно, без ключа |
| `azure` | весь каталог 700+, включая Dragon HD Omni | **даром вместе со звуком** | быстро | ~$22 / 1M символов |
| `qwen` | да, и клонирует | нужен выравниватель | быстро | ~$13 / 1M символов, 1M бесплатно на 90 дней |
| `qwen-local` | только клонирование | нужен выравниватель | RTF ~5 на процессоре | бесплатно, 2,3 ГиБ весов |

На таймингах слов держатся субтитры и монтаж дорамы. Edge и Azure сообщают их сами; два движка Qwen — нет, поэтому небольшой распознаватель снимает их с готового звука — поставь его заранее: `slopgen models install vosk-ru-small`. Он ничего не расшифровывает (слова уже известны, его спрашивают только о том, *когда* прозвучало каждое) — поэтому 46 МиБ хватает; по замерам против собственных таймингов edge-tts он попадает в ~18 мс в среднем.

**Моделей в репозитории нет.** `slopgen models list | install <id> | remove <id> | path` качает их в `paths.models` (в gitignore), печатая точный размер и лицензию до начала загрузки, а зависимости pip ставит отдельным явным шагом. Оборванная установка продолжается с того байта, на котором остановилась. То же самое — на экране **Модели** в TUI.

**Клонированные голоса** лежат в `configs/voices/` как карточка плюс звуковой образец рядом с ней:

```toml
# configs/voices/марта.toml
name = "марта"
lang = "ru"
default = "обычная"                # какой подачей говорит `--voice марта`

[samples.обычная]                  # все записи лежат в одном ряду…
ref  = "марта.обычная.wav"         # файл, рядом с этой карточкой
text = "…ровно то, что в нём сказано, набранное руками…"

[samples.зло]                      # …а это тот же человек, но в крик
ref  = "марта.зло.wav"
text = "…ровно то, что сказано в ЭТОЙ записи…"
description = "кричит, давит на каждое слово"
```

```bash
slopgen voices add sample.m4a --name марта --text "…" --clean   # проверит, сконвертирует, запишет карточку
slopgen voices check sample.m4a --text "…"                       # просто измерить
slopgen voices check марта                                       # …или готовую карточку, вместе с расшифровкой
slopgen voices record марта злой.wav --as зло --text "…"         # ещё одна подача того же человека
slopgen voices default марта зло                                 # …и теперь карточка по умолчанию кричит
slopgen voices rename марта:зло ярость                           # переименовать подачу…
slopgen voices rename марта Марта                                # …или всю карточку
slopgen drama ru --tts-engine qwen-local --voice марта            # той подачей, на которую указывает карточка
slopgen drama ru --tts-engine qwen-local --voice марта:зло        # …именно этим дублем, что бы ни было основным
```

Обучения нет — клонирование zero-shot, поэтому карточка **и есть** голос и работает на любом клонирующем движке. **Годный образец — это один непрерывный кусок речи одного человека, расшифрованный руками с этой самой записи.** Не нарезка, не сцена с музыкой под голосом, не клип с паузами: модель получает образец и расшифровку вместе с каждой репликой, и если расшифровку в звуке она не находит, то дочитывает её вслух — посреди твоего сценария. Замерено на 33-секундной нарезке, где 60% тишины, а из 51 слова расшифровки находятся 5: три дубля из трёх пятисловной реплики вернулись, произнося образец. Поэтому прогон с клонированием слушает свой референс до того, как что-то озвучить, и, если образец получает плохую оценку, озвучивает одну короткую реплику — отказывается он только тогда, когда модель её не произносит. Сама оценка не отказывает никогда: распознаватель, не разбирающий детский голос, шёпот или тяжёлую обработку, поставит плохую оценку совершенно годному образцу, а подозрение — не основание отвергать чужую запись (`[tts] check_reference = false` выключает проверку целиком); `slopgen voices check <имя>` задаёт тот же вопрос образцу, который ты только присматриваешь. Чего в этом списке НЕТ, так это длительности. Пять чистых референсов одного голоса — 6,1с, 12,2с, 24,1с, 43,4с и 61,3с — дали тридцать дублей, неотличимых друг от друга (медиана 0,81–0,82, ни одного утёкшего слова ни на одной длине). Длинный образец стоит не качества, а времени: около 0,4с лишнего синтеза на каждую секунду референса, и так на каждой реплике. Ещё две вещи стоит знать. Расшифровка набирается руками намеренно: ошибки распознавателя не остаются на месте, и с неверной расшифровкой модель, по замерам, начинала произносить слова *из образца* посреди реплики. И образец проверяется при добавлении, а не при рендере: клиппованный или шипящий референс не падает с ошибкой, он тихо портит каждую реплику каждого видео, сделанного с ним, — поэтому `voices add` отказывает образцам, про которые известно, что они ломают клонирование, а просто шумным предлагает `--clean` (RNNoise через ffmpeg — `slopgen models install rnnoise-sh`).

**Интонация — это ещё одна запись, а не параметр.** Ни у одного клонирующего движка здесь нет ручки «злее» или «шёпотом»: модель повторяет подачу того образца, который ей показали, — поэтому заставить голос кричать можно только дублем, где этот человек кричит. Значит, карточка — это **каталог** дублей одного человека: `[samples.<имя>]`, все в одном ряду, и ни один из них не хуже того, который ты нарезал первым, — а `default` называет тот, которым говорит весь ролик. Адресуются они в том же пространстве имён на уровень ниже: `марта:зло` — эта подача, а просто `марта` — та, на которую карточка указывает сейчас; и то и другое идёт всюду, где идёт имя голоса (`--voice`, `voices` у типа контента, демо-озвучка, одна строка на брейкпоинте озвучки).

Разница между этими двумя написаниями — и есть вся затея. `марта:зло` приколото к записи и там и остаётся. `марта` — это указание: «та, которая у карточки основная», поэтому перенести указатель (`slopgen voices default марта зло` или кнопка ★ в браузерной комнате голосов) значит изменить то, чем выходит каждый прогон и каждая неприколотая строка, — а перенести обратно значит вернуть их. И это не переименование, а переозвучка: запись входит в ключ кеша озвученных реплик, поэтому строки, которые шли за основной, синтезируются заново новой подачей, а приколотые по имени остаются в точности как были. Строка, приколотая в монтажной (`Scene.voice`), — тот же механизм, но на одну реплику: переколоть одну строку значит переозвучить одну строку, а не сорок; ровно так же уже работает скорость речи.

**Переименование.** Имя — это поле: и у карточки, и у каждой интонации в браузерной комнате голосов, а «сохранить» его применяет; из терминала то же делает `slopgen voices rename <карточка|карточка:подача> <новое>`. С собой оно уводит всё, что является НАСТРОЙКОЙ: файл карточки, записи, названные по ней (wav, который называет и другая карточка, остаётся на месте), указатель на основную подачу и каждый тип контента, который называл этот голос. Чего оно не уводит — приколотую строку внутри прогона: `Scene.voice` в чекпойнте, `--voice`, уже набранный в команде. Это записи о конкретном ролике, а не настройки, поэтому прогон, возобновлённый после переименования, падает на стадии озвучки и называет причину — вместо того чтобы молча произнести сорок строк не той подачей. Переименовывай до того, как приколол, а не после.

**А расставить их может нейронка.** `--deliveries` (галочка на форме запуска и в настройках монтажной) добавляет один запрос к модели до того, как что-то озвучено: готовый сценарий уходит вместе с подачами карточки и их описаниями — это весь словарь, который ей дан, — а возвращаются исключения: строки, которые кричат или шепчут, приколотые к той записи, которая это делает. Остальные остаются с пустым адресом, то есть с основной подачей, и поедут за ней, если её потом перенести. Три вещи здесь принадлежат коду, а не модели: имя подачи, которой никто не записывал, отбрасывается, а не разрешается; доля строк, которые можно переколоть, ограничена третью (ролик, переключающийся каждую третью строку, звучит не выразительно, а как смена рассказчика); и то, что приколото **руками**, уходит модели как контекст и не перезаписывается никогда — различает их `Scene.voice_auto`, а монтажная помечает выбор нейронки значком `∿`. Автоматический выбор на строке, текст которой ты с тех пор переписал, снимается при следующем входе в стадию. Описывай дубли, когда режешь их (`description` у каждой секции `[samples.<имя>]`): `зло` и `тише` значат ровно то, что ты в них вложил, и эта фраза — всё, что модель знает о записи.

Две вещи стоит знать до нарезки. **Режь дубли из ОДНОЙ сессии**, лучше из одной непрерывной записи: тембр едет вместе с подачей, и образец, записанный ближе к микрофону или в другой день, клонируется как немного другой человек — а ролик, который переключается между такими двумя, меняет рассказчика посреди фразы. И покупается этим контроль над РЕПЛИКОЙ, а не над словом: вся строка выходит в подаче того образца, которым её озвучили. Надавить на одно слово внутри строки — это дело SSML, а SSML здесь есть только у Azure.

**Можно и вовсе без движка.** `--tts-source manual` выписывает текст и ждёт, пока ты положишь `scene_NN.wav` в `manual_voice/inbox/` прогона — ровно как `--manual` для видеоряда: для хороших голосов без API и для того, чтобы начитать самому. Тайминги даст распознаватель.

## Конфиги (`configs/`)

Всё — редактируемый руками TOML; новый файл в папке = новая сущность без кода:

- `slopgen.toml` — глобальный (видео, целевая длительность, сабы, музыка, активный LLM-профиль, порядок провайдеров футажа, язык/тема интерфейса) и `[tts.pronounce.<язык>]` (см. ниже);
- `content/*.toml` — типы контента: брифы промптов по языкам, голоса edge-tts, fallback-ключевые слова;
- `ads/*.toml` — рекламные контракты: ссылка, секция overlay (ассеты, подпись, позиция, тайминг), секция native (ассеты, talking points для вплетения в озвучку), сниппет для описания; `modes` говорит, какая из двух половин вообще может отработать, и конвейеру нужны и она, и сама секция. В браузере тот же файл правится в **Конфигурация → Рекламные контракты**, и там на каждую половину приходится один тумблер: он пишет секцию и режим вместе, так что разойтись им негде. Баннер рисуется в кадре в своей настоящей ширине и в своём углу, `{url}` подставляется под описанием прямо по мере набора, а папка с ассетами пересчитывается там же, где названа, — иначе пустая всплывает четырьмя стадиями позже, посреди прогона. Контракт можно и **вписать прямо в стартовую форму, а не выбирать**: последний пункт списка контрактов — «— вписать вручную —», и под ним открываются ссылка, надпись в углу с её позицией и таймингом и тезисы для нативной вставки. Никуда он не сохраняется — он принадлежит одному этому прогону, как и кастомный профиль видеоряда, — а материал кладётся в `assets/ads/manual/overlay` и `.../native`, которые создаются за тебя при запуске. Пишутся всегда обе половины, а какую из них потратит ролик, решает `--ad-mode`, поэтому переключение overlay↔native никогда не требует перенабирать контракт. Предлагают его формы всех трёх режимов: реклама — единственное в ролике, что никак не зависит от того, каким режимом он сделан;
- `accounts/*.toml` — площадки публикации + их дефолты;
- `presets/*.toml` — бандлы параметров для запуска одной командой.
- `characters/*.toml` — каст ИИ-дорамы (`name`, `age`, `appearance`, компилируемый `visual_prompt`).
- `fandoms/<имя>/` — **папка**, а не файл, потому что мир — это больше, чем настройки: `fandom.toml` (`docs` в порядке чтения, `tone`, `lore_tool` плюс машинные `canon` и `docs_sha`), один или несколько `.md` с лором и `characters/*.toml` — собственный каст мира, лежащий рядом с миром, которому принадлежит, и записанный не так, как общий `characters/`: `appearance` плюс `plurality` (`one` / `many` / `class`), без возраста и без характера, потому что персонаж мира — это внешность, а всё остальное про него — лор. Имя папки и есть имя фандома; TOML необязателен.
- `effects/*.toml` + картинка, клип или звук рядом с каждым — база эффектов: что карточка **умеет**, пока стоит (`anchor` point/screen/full, `place`, `width`, `hold`, `fill`, `volume`, `keys` — у каждого свой `rotate` — анимация моментами — и `loop_from`/`loop_to`/`loops`: два разделителя и число повторов). База одна на все миры; какая карточка что вызывает — написано на карточке. См. [базу эффектов](#база-эффектов-configseffects).
- `orchestration/*.toml` — цепочки ИИ-генераторов для дорамы (упорядоченные `[[stages]]` с `model`/`key_mode`/`key`/`metric`/`amount` и необязательным `clip_seconds` на этап); в `model` этапа можно поставить ещё и `manual` или `search` — это оператор, а не генератор.
- `visuals/*.toml` — профили видеоряда: источник фона, привязка, ИИ-модель, интервал, движение, непрерывный режим, флаг `manual` (материал даёшь ты: для стока — найденный, для ИИ — сгенерированный), передние вставки — описаны ниже.
- `llm/*.toml` — подключения к нейронкам (`provider`, `model`, `key_env`, `temperature`, `web_search`); активное называется в `slopgen.toml` `[llm].profile`.
- `voices/*.toml` плюс образцы рядом с каждым — клонированные голоса (`lang`, `default` и по одной секции `[samples.<имя>]` на каждую подачу этого человека, у каждой свои `ref`, `text` и опциональный `ref_url` для облачного клонирования). В gitignore: это голос реального человека, а не настройка.

## Ассеты (`assets/`)

`ads/<контракт>/overlay/` — угловые анимации (.webm с альфой, .gif, .png); `ads/<контракт>/native/` — готовые рекламные вставки; `music/` — фоновые треки (один на ролик, тихо подмешивается), а `music/<полка>/` — они же по папкам, если хочешь, чтобы прогон тянул только из одной; `fonts/` — шрифты сабов; `footage/` — локальные клипы для провайдера `local`; `footage/gameplay/` — фоновые лупы для профиля `gameplay`; `images/` — локальные картинки для фото-фона и вставок. Текущие демо-файлы — заглушки для теста, замени их настоящими.

**Музыка** — один трек на ролик, подмешанный под голос на громкости `[audio] music_volume`. Какой именно — такая же настройка, как остальные: `--music sweden.m4a` в командной строке, выпадающий список на каждой стартовой форме, своя колонка в очереди цикла для одного ролика из пачки и та же ручка в монтажной, где он ещё и играет. Оставишь пустым — трек выпадет сам, но жребий посеян на прогоне, а не на часах: один и тот же прогон всегда вытягивает один и тот же трек — именно поэтому монтажная играет то, что окажется в рендере. `none` — тишина.

Разложишь папку по **полкам** — и жребий сузится до одной. Любая подпапка `assets/music/`, в которой лежит трек, предлагается отдельным выбором и пишется со слешем на конце: `эпичное/` тянет с этой полки, `эпичное/opening.m4a` — именно этот трек, а трек, лежащий просто в корне, так и остаётся `sweden.m4a` — поэтому всё, что было выбрано до появления полок, по-прежнему находит тот же файл. Вложенность разрешена, и полка покрывает всё, что под ней. Жребий при этом тот же самый: посеян на прогоне, так что и с выбранной полкой монтажная и рендер сходятся на одном треке. Пустая полка не предлагается вовсе — ручка, которую можно поставить только в тишину, не ручка, — а опустевшая с тех пор бросает жребий по всему каталогу.

## Произношение (`[tts.pronounce.<язык>]`)

Несколько слов озвучка произносит неправильно, как их в сценарии ни напиши: edge-tts читает кириллическую аббревиатуру словом всякий раз, когда её буквы складываются в произносимый слог, — и «НЛО» звучит как «нло» вместо чтения по буквам. Список обязан быть явным, потому что правило тут не поможет: то же самое чтение правильно для «ВУЗ» и неправильно для «НЛО». Всё остальное русский нормализатор уже умеет (замерено: «Лада-2107» и «18-летие» разворачиваются сами), так что таблица короткая, и дополняешь её ты.

```toml
[tts.pronounce.ru]
"НЛО" = "эн эл о"
```

Части разделяются **пробелами**; дефисы не работают. В связной речи «эн-эл-о» занимает 0.26s — ровно столько же, сколько сломанное «НЛО», потому что нормализатор схлопывает дефисную цепочку обратно в один слог; «эн эл о» через пробелы занимает 0.62s и действительно читается по частям. Какая из пробельных форм лучше — зависит от слова: голые буквы «Н Л О» здесь дают 1.10s, но на других аббревиатурах наоборот выигрывают у названий букв, так что пробуй обе. Замену видит только голос: в субтитрах остаётся исходное слово, склеенное обратно из кусков, с точными началом и концом — ничего не пересчитывается на глазок. Это зеркало `--clean-subs`, где наоборот: голос сохраняет всё, а меняется только текст на картинке.

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
