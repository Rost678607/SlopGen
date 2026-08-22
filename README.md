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
| `-b, --break STAGE` | stop for review after this stage (repeatable): `idea` `script` `tts` `footage` `subtitles` `assemble` `metadata`      |
| `--subs`         | subtitle style: `word_pop` / `phrases` / `karaoke`                                                                      |
| `--tts-rate N`   | speech rate offset in percent (-50 … +50); a slider on the Content step, and a single fragment can be re-voiced at another speed at the `tts` breakpoint |
| `--clean-subs`   | swap profanity out of the burned-in subtitles; the voiceover keeps every word                                           |
| `--out DIR`      | output dir override                                                                                                     |
| `--dry-run`      | generate but don't publish (dev tool; picking "save locally" does the same)                                             |
| `--keep-temp`    | keep intermediate ffmpeg files                                                                                          |

**`drama LANG [flags]`** — shares `--ad`, `--ad-mode`, `--profanity`, `--push`, `-n/--count`, `--subs`, `--clean-subs`, `--tts-rate`, `-b/--break`, `--out`, `--dry-run`, `--keep-temp` with `info`, plus:

| Flag                   | Meaning                                                                        |
| ---------------------- | ------------------------------------------------------------------------------ |
| `LANG`                 | positional: narration language (`en`/`ru`)                                     |
| `--scenario "..."`     | the plot/premise; omit to let the LLM invent one                               |
| `--cast A,B`           | comma-separated character names from `configs/characters/`                     |
| `--orchestration NAME` | AI-generator chain from `configs/orchestration/` (default: one `wan2.1` stage) |
| `--duration-min N`     | target length in **minutes**                                                   |
| `--clip-s N`           | AVERAGE length of one generated clip in seconds (0 = each generator's own); the writer sizes each beat around it |
| `--visual-notes "…"`   | constraints on what the shots may SHOW, never on the story ("all weapons are toy ones") |
| `--clean-subs`         | swap profanity out of the burned-in subtitles; the voiceover keeps every word |
| `--tol N`              | how many **seconds** the finished video may run over/under the target          |
| `--parts N`            | split one drama into N publishable parts; script cuts are planned as cliffhangers. Only what is *asked* of the writer — the boundaries are then yours to move at the `script` and `cut` breakpoints |
| `--parts-at-once`      | cut every part together at the end, instead of finishing each as soon as its own clips are in |
| `--voice ID`           | edge-tts narrator voice (default per language)                                 |
| `--tts-rate N`         | speech rate offset in percent (-50 … +50). The writer counts on it: a faster voice fits more story into a clip of the same length, so each beat is written longer |

**Global** (before the mode, or standalone): `--resume DIR`, and the inspectors
`--list-types` `--list-ads` `--list-accounts` `--list-presets` `--list-visuals`
`--list-characters` `--list-orchestrations`.

**Subcommands** that reopen a run which parked itself, each straight into the screen
it is waiting on: `slopgen gather [DIR]` (hand-made clips) and `slopgen review [DIR]`
(a breakpoint). Omit the directory and the latest such run is found.

Parameter priority (info mode): **CLI flags > preset > account defaults > global defaults**. An account config can carry its own default language/type/ad, so `slopgen info --push yt_main` alone is a valid command. Drama builds its parameters directly from its own flags (no preset/account merge yet).

**Crash recovery.** Every run is checkpointed to `<out>/<stamp>_<type>_<lang>/checkpoint.json` after each pipeline stage. If a run dies partway (network drop, killed process), the finished stages' outputs (TTS audio, downloaded footage, the job state) are kept, and the failing stage + error are recorded. Re-run with `slopgen --resume <that dir>` to skip the completed stages and continue from the point of failure — already-finished videos are left untouched, unfinished ones pick up where they stopped. When a run ends with failures, the summary prints the exact `--resume` command to use.

**Breakpoints.** Tick any pipeline stage on the wizard's **Summary** step (or pass `--break STAGE`, repeatable) and the run parks right after that stage instead of walking on — the checkpoint holds it in a `review` state, and a review screen shows what the stage produced as a list of editable lines:

| Breakpoint  | What you get                                                                                            |
| ----------- | ------------------------------------------------------------------------------------------------------- |
| `idea`      | the chosen topic, before a single line is written                                                       |
| `script`    | the script as written — per scene: the spoken line, its shot prompt (or search terms), the cast in frame, the generator and the clip length. The only place to fix a shot before it is generated |
| `entities`  | *(drama)* the visual registry: everything that recurs across shots and is not cast — a machine, a location, a prop, a nameless regular, an unusual crowd. Per entry: the name the shot prompts use for it, a note, and the English descriptor the generator gets. Editing one descriptor restyles every shot showing it |
| `tts`       | every voiced fragment with the length that came out; edit a line and **🔊 re-voice** it right there (▶ to listen), as many takes as you like — only that line is re-synthesized. A **speed slider** sits above the buttons: one for the whole screen, applying to whichever fragment you re-voice with it. That line then keeps the speed (and the card says so) while the rest of the video stays at the run's |
| `cut`       | *(drama)* where each episode ends. The scenes are read-only here, each showing the seconds it really runs to; what you move are the **part markers** — the last free moment to re-cut, since the next stage generates (or asks you to hand-make) the clips against these boundaries |
| `footage`   | the shot prompt (drama) or search queries (info) per scene; changed scenes get their footage remade      |
| `subtitles` | the generated `.ass` files as text, written straight back to disk                                       |
| `assemble`  | the rendered file(s) — inspect-only, watch them before publishing                                       |
| `metadata`  | title, description and tags, right before publish                                                       |

The screen is master-detail: the stage's items are **cards** on the left — **＋** adds one, **▲ ▼** reorder, **✖** drops — and the open card's fields are edited on the right. A drama scene carries its spoken line, its shot prompt, who is in it (added and removed one by one from the run's cast), which generator makes it and how long the clip runs. Fields are editable by hand (except the `.ass` files, where a wholesale model rewrite would mangle the cue timings), and an **AI edit line** sits under the list. At the `script` breakpoint it works on the scene list as a whole and may do anything the instruction asks — rewrite any field, reorder, merge, split, add or drop scenes, recast them, switch generators — carrying each scene's identity along so an untouched one keeps the audio and clip already made for it. Elsewhere it rewrites the free-text lines: describe the change ("shorter", "make scene 3 angrier", "split this beat in two") and the model rewrites the whole set — for script/voiceover it may also change how many fragments there are. A drama also shows **part markers** among the cards — `── part 2 starts here ──` — and everything below one belongs to that episode. On `script` and `cut` they are yours to move (**▲ ▼**), to add (**＋ Part break**, splitting the drama further) and to drop (merging an episode into the one above); everywhere else they are drawn read-only, so you can always see which episode you are looking at. This is what sets the number of parts — `--parts` only asks the writer for a starting point. Press **Continue** and the run picks up from there; a breakpoint fires once per video, so a re-run of the stage you just edited won't park again. With `-n` >1 the videos queue up and are reviewed one after another. Both modes support it. Headless runs print `slopgen review <dir>` to reopen the parked run (same as `slopgen gather` for manual clips).

## TUI

`slopgen` with no arguments. Custom **Minecraft theme**, no footer — the top bar holds the RU/EN interface-language toggle, the `<-` back button and the command Palette.

- **Home** — centered menu, arrow keys + Enter.
- **Generate** — first pick a mode (**minute-of-info** or **AI drama**), then a step-by-step wizard with a vertical step list on the left. *Info:* 1) content (language, narrator voice, type, your own idea, profanity and speech-rate sliders), 2) visuals (profile + full overrides: background source/linkage/interval/Ken Burns, foreground inserts; target duration), 3) ads (a saved contract *or* fully manual fields), 4) publishing (account, count, subtitle style, clean-subtitles switch), 5) summary with the equivalent CLI command, the breakpoint switches and the GENERATE button. *Drama* adds a **Story** step (plot + a reorderable cast, edited on the right, with photo→appearance vision and AI cast-fill), puts clip length, the speech-rate slider and visual constraints on **Content** and a parts field on **Publishing**, and turns the Visuals step into **orchestration** (an ordered list of AI generators; see below). Set everything up, press it, walk away.
- **Review** — where a run parked itself: the breakpoint screen (cards on the left, the open card's fields on the right, an AI edit line under them) or the manual-clip gather screen. Both resume the run when you are done; `slopgen review` / `slopgen gather` open them directly.
- **Progress** — while a run works, a bar tracks the stage it is inside: voiced fragments, generated video fragments, assembled scenes and rendered files, each as `done/total`, over a per-video queue table and a live log.
- **Configuration** — sections on the left: LLM profiles (profile tabs, per-provider model presets, API-key input auto-saved to `.env`, ★ activation), footage/generator keys, the character library, ad contracts, accounts, presets. Entity sections have a tab per existing config file on top plus `+ new`; forms are prefilled, with 💾 save and 🗑 delete (confirmed).
- The chosen color theme persists across runs (`[ui].theme`).

## AI drama (`configs/characters/`, `configs/orchestration/`)

A second mode: a **narrated web drama** — one voiceover narrator tells a story (and may quote characters' lines inline) over AI-generated shots featuring a recurring cast.

- **Cast** (`configs/characters/*.toml`): `name`, `age`, `appearance`. Before generation each character is compiled once into a token-dense English `visual_prompt`, which is substituted **in place of that character's name** wherever the shot description mentions them — names never reach the generator (it cannot map a name to a face, and a foreign name gets rendered as literal text across the frame), and binding the description to the person doing the action keeps two characters in one shot from being blended or swapped (a text-only anchor — free generators won't lock a face perfectly). In the TUI you can build an ad-hoc cast, pull members from the library, upload a photo (vision → appearance), and let the AI fill the whole cast from the premise.
- **Visual registry** (the `entities` stage): the cast pins how *people* look; nothing pins anything else a story reuses. Named once in one shot and once in another, a transforming robot-house, a particular car or the kitchen everything happens in is drawn from scratch every time — which is how "robot-house" comes back as a plain robot. So once the script exists, a pass over every shot prompt collects whatever appears in **more than one shot** and is not cast, and describes it once. It is deliberately **untyped**: the model decides what is worth pinning — a machine, a building, a prop, a recurring person who never made the cast, even a crowd when it is a specific one (uniforms, placards) rather than passers-by — and the `kind` it writes is a label for you to read, nothing branches on it. Each entry's descriptor is then substituted in place of its name exactly like a character's. The same pass makes the prompts usable: a shot showing a registered thing must call it by its registered name, every cast member the writer listed as present must actually be *named* in the prompt (an unnamed one cannot be substituted into, and the look ends up appended loose, bound to nobody), and every prompt must say **where** it happens — a prompt that is only people and a verb renders as those people standing in an empty room. Review and edit the whole registry at the `entities` breakpoint; one descriptor restyles every shot showing it.
- **Prompt budget**: appearance must not swamp the shot. Three characters at ~20 descriptor tags each leave the action as a rounding error, and the generator draws three people matching their descriptions with nothing left for what they are doing. So a shared frame splits one appearance budget between the people in it (two get 6 tags each, three get 4, with a floor of 3 that keeps a face recognisable between shots); a lone character is never trimmed, and neither is a registry entry, whose descriptor is the only thing saying what an invented compound looks like. A character the shot mentions only as an owner (`Игнат's robot-house`) is not in the frame and contributes no look — otherwise a man with a tool belt gets glued onto a building.
- **Orchestration** (`configs/orchestration/*.toml`): an ordered list of AI generators, each a `model` (`wan2.1`/`ltx-video`/`animatediff` video, `flux`/`turbo` image), a `key_mode` (`rotate` keys on a limit / `single` key then skip), a `metric`+`amount`, and an optional `clip_seconds`. The pipeline walks the stages in order and each makes its share of the clips: `percent` = a share of the length budget, `seconds`/`clips` = an absolute chunk, and the last stage fills the remainder. Multiple API keys (one per line in `.env`) are rotated across stages. A stage's `clip_seconds` overrides the run-level average — handy when one stage's clips are longer than the rest (hand-made Kling/Veo shots next to 5-second Space clips).
- **Length, parts & sync**: authored in **minutes** + a **tolerance** in seconds (the story may run a bit over/under). If parts >1 the **cuts are planned up front**, as beat numbers, by the outline pass below — and the window that owns a cut is told which of *its own* beats ends an episode, so that beat is written as a cliffhanger and the next one opens on the fallout. Which episode a beat lands in follows from the plan, not from a label the writer guessed at. **Clip length** is authored too (`--clip-s`, or the field on the wizard's Content step; 0 = each generator's nominal ~3-5s). It decides how many clips the budget is cut into and how much narration each carries — a 7-minute drama is 84 clips at 5s but 28 at 15s. The length is **fixed** — the writer never changes it, it only writes to it. What varies is how much story a beat holds: a drawn-out moment is told to run across several consecutive beats rather than being crammed into one, and a fast turn to be compressed into a single beat. Padding a beat with three unrelated actions to fill the time is exactly what reads as monotonous. The script is written a **window** of ~14 beats at a time, not all at once: asked for a whole feature-length script in one response a model spends its attention front-loaded, tracking the premise sentence by sentence at the start and turning to summary after — dropping named props, sub-plots and reversals you wrote down. Windows alone were not enough, and this is where a long drama used to come apart after the middle: told only that its beats sit *about 55% of the way through the premise*, a window has to eyeball which sentences of a two-thousand-word brief that is, and it eyeballs badly — the middle windows re-tell what an earlier one covered, skip what lies between, and the back half runs on what the model remembers instead of on your brief. So the brief is cut up **first**, by an **outline pass** that reads all of it in one call: per window it fixes what happens in that stretch, the checklist of concrete details from your brief that stretch owns and no other may spend (names, numbers, props, places, spoken lines), and where the story stands when it ends. Each window then writes to its own stretch, with the whole outline in front of it so it can see what is already told and what the windows after it are waiting for. The same pass chooses where the episodes are cut. Windows are balanced, so 30 beats are 15+15 rather than 14+14+2, because a two-beat last window is a whole call asked to write the ending in two beats; a single-window drama sees everything at once and skips the outline, and so does a run whose outline comes back unusable — it falls back to the percentage slices. Note that more *minutes* alone does not buy more detail: what a beat can hold comes from **clip length**, since narration is sized per beat (6s ≈ 12 Russian words at the natural speed, one short sentence). A detail-dense plot wants longer clips, not just a longer runtime. **Voicing speed** (`--tts-rate`) is part of that arithmetic rather than a cosmetic afterthought: a beat voiced at +30% takes a third more words to fill the same shot, so the writer is told the budget at the speed the run will actually speak, and the voice/picture fitting below is left with only the residual mismatch. Any single fragment can still be re-voiced at its own speed at the `tts` breakpoint. Whatever the length, a beat is ONE continuous take that moves, never a list of cuts: told `wide shot THEN close-up THEN reaction`, real generators read that as a storyboard and open the clip with every shot on screen at once, as a split-screen grid. Every prompt also carries an explicit single-frame clause, because generators reach for that layout unprompted. One beat equals one clip, and the two are fitted to each other in stages, cheapest change first. The **voice** moves before the picture does, since nobody sees it happen: up to ±25% it absorbs the mismatch alone and the clip plays untouched. Past that the voice sits at its comfortable edge and the **picture** slows to cover the rest (down to 45% speed — that reads as deliberate slow motion); only when the picture is spent as well is the voice pushed to its hard limit. A 20s line over a 15s clip is voice-only; a 45s one becomes a 1.35× voice over a 0.45× clip, still matching exactly. The other direction is not symmetric: a voice **shorter** than its clip is left alone and the surplus picture is simply cut off, which costs nothing and shows nothing — retiming there would buy a sluggish voice or a comic clip and still trim afterwards. Loading it all onto the voice is what used to leave the clip short, and a short clip **restarted from its beginning mid-scene**. Subtitle timings are rescaled to match — audio and video stay locked. A native ad, when enabled, is woven into the plot at the script level rather than bolted on.

- **One episode at a time**: a part is a publishable video of its own, and the pipeline finishes it as soon as it can rather than waiting for the whole drama. This matters on the user-assisted path, where you make the clips by hand in some web tool and the free daily limits run out long before the story does: gather episode 1's clips, resume, and it is cut, subtitled, described and published while episodes 2 and 3 sit untouched. Come back tomorrow with more clips and `slopgen gather` picks up exactly there — the parts already done are never re-cut and their clips never re-generated. Each part gets its own timeline (its subtitles start at 0:00), its own file (`part_02.mp4`), its own `metadata_part_02.json` written by a model that is told which episode of how many it is describing, and it goes out the moment it is ready. An upload is recorded on the part itself, so no amount of resuming can publish it twice. The gather screen shows a **part** column and, per episode, either `part 1 ✔ ready to cut` or how many clips it is still short — **Finish & resume** needs one complete episode, not all of them. Nothing stops you carrying on: while a part renders you can keep generating clips and dropping them into `manual/inbox/`, and the next resume finds them. If you would rather have the whole drama land together — or watch all of it before any goes out — turn the **Finish parts one at a time** switch off (`--parts-at-once`) and nothing is cut until the last clip is in, which is how it worked before.

- **Censorship & framing**: two switches that keep a run publishable. **Visual constraints** (`--visual-notes`) bind what the shots may SHOW and nothing else — the story is written as if they did not exist, so "all weapons are toy ones" leaves the gunfight a gunfight and only changes the props; they reach the writer and ride along on every generated prompt. **Clean subtitles** (`--clean-subs`) swaps profanity out of the burned-in text while the voiceover keeps every word — platforms moderate what they can read. Whole lines are rewritten, not single words — «Съебал нахуй с моей пары пидорас блять» becomes «Уйдите пожалуйста с моей пары молодой человек», where word-by-word swapping would leave a limping sentence. It catches words that merely look profane, such as the first part of the name «Хуй Сунь Вынь», and since a rewrite changes the word count, the line's span is re-divided among its new words so it still starts and ends with the speech. Cost is bounded: lines are screened by regex first, so a clean video makes no request at all, and only the flagged lines travel (each with its immediate neighbours for context) in a single request per video. Replies are keyed by line number, so a partial one still lands what it returned and only the rest fall back to masking.

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

- `slopgen.toml` — global: video size/fps, target duration, subtitle style/font/colors, music volume, active LLM profile, footage provider order, UI language/theme, defaults, and `[tts.pronounce.<lang>]` (below).
- `content/*.toml` — content types: per-language creative briefs (`idea_brief`, `script_brief`), edge-tts `voices`, `fallback_keywords` for stock search.
- `ads/*.toml` — ad contracts: `url`, overlay section (assets dir, caption `text`, `position`, `start_s`, `duration_s`, `width`), native section (assets dir, `talking_points` the LLM weaves into the script), description `snippet` (`{url}` is substituted).
- `accounts/*.toml` — publishing targets: `platform`, YouTube OAuth paths/privacy/category, optional `defaults` (lang/type/ad).
- `presets/*.toml` — full parameter bundles for one-command runs.
- `characters/*.toml` — AI-drama cast members (`name`, `age`, `appearance`, compiled `visual_prompt`).
- `orchestration/*.toml` — AI-drama generator chains (ordered `[[stages]]` with `model`/`key_mode`/`key`/`metric`/`amount`, plus an optional per-stage `clip_seconds`).
- `visuals/*.toml` — visuals profiles: background source/linkage/AI model/interval/motion/continuous, foreground inserts, described below.
- `llm/*.toml` — LLM connections (`provider`, `model`, `key_env`, `temperature`, `web_search`); the active one is named in `slopgen.toml` `[llm].profile`.

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

**Pronunciation** (`[tts.pronounce.<lang>]` in `configs/slopgen.toml`). A few words come out wrong however they are spelled in the script: edge-tts reads a Cyrillic acronym as a word whenever its letters happen to form a pronounceable syllable, so «НЛО» is said "нло" instead of being spelled out. It has to be an explicit list, because no rule separates the cases — the same reading is correct for «ВУЗ» and wrong for «НЛО». Everything else its Russian normalizer already handles (measured: «Лада-2107» and «18-летие» both come out fully expanded), so the table stays short and is yours to extend.

```toml
[tts.pronounce.ru]
"НЛО" = "эн эл о"
```

Separate the parts with **spaces**; hyphens do not work. Measured in running speech, «эн-эл-о» takes 0.26s — exactly as long as the broken «НЛО» — because the normalizer collapses a hyphenated run back into one syllable, while the spaced «эн эл о» takes 0.62s and is genuinely spelled out. Which spaced form reads best is per-word: bare letters «Н Л О» run 1.10s here, yet beat the phonetic names on other acronyms, so try both. Only the voice sees the respelling: the subtitles keep the original word, merged back from the pieces it was spoken as, with its exact start and end — so nothing is re-spread or estimated. This is the mirror of `--clean-subs`, where the voice keeps every word and only the burned-in text changes.

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

Первый позиционный аргумент — **режим**: `info` (ролик-минутка) или `drama` (ИИ-дорама); он меняет остальную часть команды. Флаги драмы: `--scenario`, `--cast A,B` (имена из `configs/characters/`), `--orchestration`, `--duration-min` (минуты), `--tol` (секунды допуска), `--clip-s` (СРЕДНЯЯ длина клипа в секундах, 0 = длина самого генератора), `--visual-notes` (ограничения картинки, не сюжета), `--clean-subs` (чистить мат в субтитрах), `--parts` (сколько частей с клиффхэнгерами просить у сценариста; границы потом двигаются на брейкпоинтах `script` и `cut`), `--parts-at-once` (резать все части в конце, а не по мере готовности), `--voice`; плюс общие с `info`: `--ad`, `--ad-mode`, `--profanity`, `--push`, `-n`, `--subs`, `--out`, `--dry-run`, `--keep-temp`, `--tts-rate` (скорость речи, ±50%; в дораме на неё рассчитывает сценарист — чем быстрее голос, тем больше сюжета влезает в клип той же длины, поэтому реплики пишутся длиннее), `-b/--break ЭТАП` (остановка на разбор после этапа, флаг повторяемый). У `info` есть свой `--visuals`. Глобальные (до режима): `--resume`, `--list-types/-ads/-accounts/-presets/-visuals/-characters/-orchestrations`. Подкоманды `slopgen gather [папка]` и `slopgen review [папка]` возвращают к застывшему прогону — к ручным клипам и к брейкпоинту соответственно; без папки берётся последний такой прогон.

**Восстановление после сбоя.** Каждый прогон пишет чекпойнт в `output/<время>_<тип>_<язык>/checkpoint.json` после каждого этапа конвейера. Если прогон оборвался на ошибке (обрыв сети, убитый процесс), пройденная часть (озвучка, скачанный футаж, состояние задачи) сохраняется, а этап и текст ошибки записываются. Команда `slopgen --resume <эта папка>` пропустит выполненные этапы и продолжит с места остановки: готовые видео не трогаются, недоделанные досчитываются. Если прогон завершился с ошибками, в итоговой сводке печатается готовая команда `--resume`.

**Брейкпоинты.** Отметь любой этап конвейера на шаге **«Итог»** в визарде (или передай `--break ЭТАП`, флаг повторяемый) — и прогон встанет сразу после этого этапа: чекпойнт переводится в состояние `review`, а экран разбора показывает результат этапа списком редактируемых строк:

| Брейкпоинт  | Что показывает                                                                                        |
| ----------- | ------------------------------------------------------------------------------------------------------ |
| `idea`      | выбранную тему, ещё до единой написанной строчки                                                       |
| `script`    | сценарий как он написан — по сцене: реплика, промпт кадра (или поисковые слова), кто в кадре, нейронка и длина клипа. Единственное место, где кадр правится до генерации |
| `entities`  | *(дорама)* реестр визуала: всё, что повторяется в кадрах и не входит в каст — техника, локация, реквизит, безымянный завсегдатай, необычная массовка. По записи: имя, которым её называют промпты, заметка и английское описание, уходящее генератору. Правка одного описания меняет вид вещи сразу во всех кадрах |
| `tts`       | каждый озвученный фрагмент с получившейся длительностью; правь строку и **🔊 переозвучивай** прямо тут (▶ послушать), хоть до посинения — переозвучивается только она. Над кнопками **ползунок скорости**: он один на весь экран и применяется к тому фрагменту, который ты им переозвучил. Эта строка дальше живёт со своей скоростью (карточка её показывает), остальное видео остаётся на скорости запуска |
| `cut`       | *(дорама)* где кончается каждая серия. Сцены тут только для чтения, у каждой видно, сколько она реально идёт; двигаешь **маркеры частей** — это последний бесплатный момент для перекройки, дальше клипы генерируются (или просятся руками) уже под эти границы |
| `footage`   | промпт кадра (дорама) или поисковые запросы (инфа) по сценам; изменённым сценам видеоряд соберут заново |
| `subtitles` | сгенерированные `.ass` как текст, правки пишутся прямо на диск                                         |
| `assemble`  | готовые файлы — только просмотр, посмотри перед публикацией                                            |
| `metadata`  | заголовок, описание и теги перед самой публикацией                                                     |

Экран устроен как мастер-детейл: слева **карточки** позиций этапа — **＋** добавляет, **▲ ▼** двигают, **✖** удаляет, — справа поля открытой карточки. У сцены дорамы это реплика, промпт кадра, кто в кадре (добавляется и убирается поштучно из каста прогона), какая нейронка её генерирует и сколько длится клип. Поля правятся руками (кроме `.ass` — переписывание файла нейронкой снесёт тайминги), а под списком — ИИ-строка. На брейкпоинте `script` она работает со списком сцен целиком и умеет всё, о чём попросишь: переписать любое поле, переставить сцены, склеить, разбить, добавить, убрать, сменить каст и нейронки — сохраняя тождество сцен, так что нетронутая сцена оставляет при себе уже сделанные озвучку и клип. На остальных этапах переписывает текстовые строки: пишешь, что поменять («короче», «третью сцену злее», «разбей этот бит на два»), и модель переписывает весь набор; для сценария и озвучки она может ещё и поменять количество фрагментов. У дорамы среди карточек стоят ещё и **маркеры частей** — `── здесь начинается часть 2 ──`, — и всё, что ниже маркера, относится к этой серии. На `script` и `cut` их можно двигать (**▲ ▼**), добавлять (**＋ Разрыв части**, разрезая дораму дальше) и удалять (склеивая серию с предыдущей); на остальных этапах они нарисованы только для чтения, чтобы всегда было видно, какую серию смотришь. Именно этим и задаётся количество частей — `--parts` лишь просит у сценариста отправную точку. Жмёшь **Продолжить** — конвейер идёт дальше. Брейкпоинт срабатывает один раз на видео, так что переделка только что отредактированного этапа снова не встанет. При `-n` >1 видео выстраиваются в очередь и разбираются по одному. Работает в обоих режимах. В headless-прогоне печатается команда `slopgen review <папка>`, чтобы вернуться к застывшему прогону (по аналогии с `slopgen gather` для ручных клипов).

Приоритет параметров (режим info): **флаги CLI > пресет > дефолты аккаунта > глобальные дефолты**. Аккаунт может нести свои дефолты — `slopgen info --push yt_main` уже валидная команда. Драма собирает параметры прямо из своих флагов (слияния с пресетом/аккаунтом пока нет).

## TUI

`slopgen` без аргументов. Тема **Minecraft**, нижней панели нет — сверху панель с переключателем языка интерфейса RU/EN, кнопкой `<-` (назад) и Palette.

- **Меню** — по центру, выбор стрелочками + Enter.
- **Генерация** — сначала выбор режима (**минута инфы** или **ИИ-дорама**), затем пошаговый визард со списком шагов слева. *Info:* 1) контент (язык, голос диктора, тип, идея, ползунки мата и скорости речи), 2) видеоряд (профиль + переопределения: фон, привязка, интервал, Ken Burns, вставки; длительность), 3) реклама (контракт *или* вручную), 4) публикация (аккаунт, количество, стиль сабов, переключатель чистых субтитров), 5) итог с CLI-командой, тумблерами брейкпоинтов и кнопкой СГЕНЕРИРОВАТЬ. *Дорама* добавляет шаг **Сюжет** (замысел + переставляемый каст, редактирование справа, фото→внешность через vision, ИИ-заполнение каста), кладёт длину клипа, скорость озвучки и ограничения картинки на **Контент**, количество частей — в **Публикацию**, и превращает шаг «Видеоряд» в **оркестрацию** (упорядоченный список ИИ-генераторов; см. ниже).
- **Прогресс** — пока идёт прогон, полоса показывает, где он внутри этапа: фрагменты озвучки, сгенерированные видеофрагменты, смонтированные сцены и собранные файлы, каждое как `сделано/всего`, над таблицей очереди по роликам и живым логом.
- **Разбор** — туда, где прогон встал: экран брейкпоинта (слева карточки, справа поля открытой, под ними ИИ-строка) либо экран сбора ручных клипов. Оба по завершении продолжают прогон; `slopgen review` / `slopgen gather` открывают их напрямую.
- **Конфигурация** — секции слева: профили нейронок (табы профилей, пресеты моделей, ввод API-ключа с автосохранением в `.env`, активация ★), ключи стока и генераторов, библиотека персонажей, рекламные контракты, аккаунты, пресеты. В секциях сущностей сверху табы — по одному на конфиг-файл плюс `+ новый`; формы предзаполнены, есть 💾 сохранение и 🗑 удаление с подтверждением.
- Выбранная тема оформления сохраняется между запусками (`[ui].theme`).

## ИИ-дорама (`configs/characters/`, `configs/orchestration/`)

Второй режим: **озвученная веб-дорама** — один закадровый рассказчик ведёт историю (и может цитировать реплики героев внутри повествования) поверх ИИ-кадров с постоянным кастом.

- **Каст** (`configs/characters/*.toml`): `name`, `age`, `appearance`. Перед генерацией каждый персонаж один раз компилируется в токен-плотный английский `visual_prompt`, который подставляется **вместо имени персонажа** там, где описание кадра его упоминает: имена до генератора не доходят (он не свяжет имя с лицом, а кириллицу отрисует текстом поперёк кадра), а привязка описания к тому, кто действует, не даёт смешать или перепутать двух персонажей в одном кадре — и внешность держится (это текстовый якорь; бесплатные генераторы не фиксируют лицо идеально). В TUI можно собрать каст ad-hoc, подтянуть из библиотеки, загрузить фото (vision → внешность) и дать ИИ заполнить весь каст по замыслу.
- **Реестр визуала** (этап `entities`): каст фиксирует, как выглядят *люди*; всё остальное, что история переиспользует, не фиксирует ничто. Названные один раз в одном кадре и один раз в другом, дом-трансформер, конкретная машина или кухня, в которой всё происходит, каждый раз рисуются заново — отсюда и «robot-house», приезжающий обычным роботом. Поэтому, как только сценарий готов, проход по всем промптам кадров собирает всё, что встречается **больше чем в одном кадре** и не входит в каст, и описывает это один раз. Реестр намеренно **нетипизированный**: что заносить, решает модель — техника, здание, реквизит, повторяющийся человек, не попавший в каст, и даже массовка, если она особая (форма, плакаты), а не просто прохожие; а `kind`, который модель пишет, — ярлык для чтения глазами, на него ничто не завязано. Дальше описание записи подставляется вместо её имени ровно так же, как у персонажа. Тот же проход делает промпты пригодными: кадр, показывающий занесённую вещь, обязан называть её реестровым именем; каждый персонаж, которого сценарист отметил присутствующим, должен быть в промпте **назван** (в неназванного нечего подставлять, и внешность оказывается дописана в конец, не привязанная ни к кому); и каждый промпт обязан сказать, **где** это происходит — промпт из одних людей и глагола отрисовывается как эти люди, стоящие в пустой комнате. Весь реестр смотрится и правится на брейкпоинте `entities`; одно описание меняет вид вещи во всех кадрах сразу.
- **Бюджет промпта**: внешность не должна забивать кадр. Три персонажа по ~20 тегов описания оставляют от действия погрешность округления — генератор рисует троих по описаниям, и на то, что они делают, у него уже ничего не остаётся. Поэтому общий кадр делит один бюджет внешности между теми, кто в нём: двое получают по 6 тегов, трое — по 4, при нижней границе в 3 тега, которая держит лицо узнаваемым между кадрами. Одинокий персонаж не урезается никогда, как и запись реестра: её описание — единственное, что объясняет, как выглядит выдуманное слово. Персонаж, упомянутый в кадре только как владелец (`Игнат's robot-house`), в кадре не находится и внешности не получает — иначе мужик с поясом инструментов приклеивается к зданию.
- **Оркестрация** (`configs/orchestration/*.toml`): упорядоченный список ИИ-генераторов — `model` (`wan2.1`/`ltx-video`/`animatediff` — видео, `flux`/`turbo` — картинка), `key_mode` (`rotate` — ротация ключей на лимите / `single` — один ключ, потом пропуск), `metric`+`amount` и необязательный `clip_seconds`. Конвейер идёт по этапам, каждый делает свою долю клипов: `percent` — доля бюджета длины, `seconds`/`clips` — абсолютный кусок, последний этап добирает остаток. Несколько API-ключей (по одному на строку в `.env`) ротируются между этапами. `clip_seconds` этапа перебивает общее значение прогона — пригодится, когда клипы одного этапа длиннее остальных (ручные кадры из Kling/Veo рядом с пятисекундными клипами Spaces).
- **Длина, части и синхрон**: задаётся в **минутах** + **допуск** в секундах (история может немного выйти за рамки). Если частей больше одной, **обрывы планируются заранее** — номерами битов, тем самым проходом-планом, что ниже, — и окно, внутри которого приходится обрыв, узнаёт, какой из *его собственных* битов закрывает серию: этот бит пишется клиффхэнгером, а следующий открывается прямо с последствий. В какую серию попадёт бит, решает план, а не метка, которую угадал сценарист. **Длина клипа** тоже задаётся (`--clip-s` или поле на шаге «Контент» в визарде; 0 = номинал генератора, ~3-5с). От неё зависит, на сколько клипов режется бюджет и сколько озвучки достаётся каждому: семиминутная дорама — это 84 клипа по 5с, но всего 28 по 15с. Длина **фиксирована** — сценарист её не меняет, а только под неё пишет. Варьируется то, сколько сюжета влезает в бит: затяжной момент ему велено растягивать на несколько подряд идущих битов, а не запихивать в один, а быстрый поворот — сжимать в один. Набивать бит тремя несвязанными действиями ради заполнения времени — это и читается монотонно. Сценарий пишется **окнами** по ~14 битов, а не целиком за раз: когда у модели просят весь полнометражный сценарий одним ответом, её внимание уходит в начало — первые биты идут по замыслу фраза за фразой, а дальше начинается пересказ, и из него выпадают названные тобой предметы, побочные линии и повороты. Но одних окон не хватило — и именно тут длинная дорама разваливалась после середины: если окну сказано лишь, что его биты идут «примерно с 55% замысла», ему приходится на глаз прикидывать, какие это фразы в брифе на две тысячи слов, и прикидывает оно плохо — средние окна пересказывают то, что уже рассказало раннее, пропускают всё, что между, и вторая половина идёт уже не по твоему брифу, а по тому, что модель запомнила. Поэтому бриф режется **сначала**, отдельным **проходом-планом**, который читает его целиком за один запрос: на каждое окно он фиксирует, что в этом отрезке происходит, список конкретных деталей брифа, за которые отвечает именно этот отрезок и никакой другой (имена, числа, предметы, места, произнесённые реплики), и то, в каком положении окажется история к его концу. Дальше каждое окно пишет по своему отрезку, держа перед глазами весь план целиком — чтобы видеть, что уже рассказано, а что ждут от него следующие окна. Тот же проход выбирает, где резать серии. Окна сбалансированы, так что 30 битов — это 15+15, а не 14+14+2: окно из двух битов — это целый запрос, которому велено уместить финал в два бита. Дорама в одно окно видит всё сразу и план не заказывает; так же поступает и прогон, у которого план вернулся негодным, — он откатывается на процентные отрезки. Учти, что одни только **минуты** детализации не добавляют: сколько влезает в бит, определяется **длиной клипа** — озвучка нарезается по битам (6с ≈ 12 русских слов на обычной скорости, одно короткое предложение). Плотному на детали сюжету нужны клипы подлиннее, а не просто хронометраж побольше. **Скорость озвучки** (`--tts-rate`) входит в эту арифметику, а не приделана сбоку: бит, озвученный на +30%, требует на треть больше слов, чтобы заполнить тот же кадр, — поэтому сценаристу называют бюджет слов на той скорости, на которой прогон реально заговорит, а подгонке голоса и картинки ниже остаётся только остаток рассогласования. Отдельный фрагмент при этом можно переозвучить на своей скорости на брейкпоинте `tts`. При любой длине бит остаётся ОДНИМ непрерывным дублем, а не списком склеек: если написать `общий план THEN крупный план THEN реакция`, реальные генераторы читают это как раскадровку и открывают клип сеткой из всех кадров сразу. К каждому промпту дописывается явное «один кадр целиком» — генераторы тянутся к сетке и без просьбы. Один бит равен одному клипу, и подгоняются они ступенями, начиная с самого дешёвого. **Голос** двигается раньше картинки — его никто не видит: до ±25% он забирает рассогласование целиком, клип идёт нетронутым. Дальше голос встаёт на комфортной границе, а остаток берёт **картинка**, замедляясь вплоть до 45% скорости (читается как намеренное слоу-мо); и только когда исчерпана и она, голос дожимается до жёсткого предела. Реплика 20с на клипе 15с — только голос; 45с — голос 1.35× поверх клипа 0.45×, и они всё ещё сходятся ровно. Обратная сторона несимметрична: если голос **короче** клипа, не трогается ничего, а лишний хвост картинки просто обрезается — это не стоит ничего и не видно, а ретайм там купил бы вялый голос или комичный клип и всё равно обрезал бы следом. Раньше всё вешалось на голос, клипа не хватало, и он **начинался сначала посреди сцены**. Тайминги субтитров пересчитываются — звук и видео синхронны. Нативная реклама вплетается в сюжет на уровне сценария, а не вклеивается отдельно.

- **По одной серии за раз**: часть — это самостоятельное публикуемое видео, и конвейер доводит её до конца сразу, как может, а не ждёт всю дораму. В этом и смысл user-assisted пути, где клипы гонишь руками в каком-нибудь веб-сервисе, а бесплатные дневные лимиты кончаются сильно раньше сюжета: собрал клипы первой серии, продолжил — и она смонтирована, засубтитрена, описана и опубликована, пока вторая и третья лежат нетронутыми. Пришёл завтра с новыми клипами, `slopgen gather` — и подхват ровно оттуда: готовые части не пересобираются, их клипы не перегенерируются. У каждой части свой таймлайн (её субтитры начинаются с 0:00), свой файл (`part_02.mp4`), свой `metadata_part_02.json`, написанный моделью, которой сказали, какую серию из скольких она описывает, и уходит она в публикацию сразу, как готова. Загрузка отмечается на самой части, так что сколько ни продолжай — дважды не зальётся. На экране сбора появляется колонка **часть**, а по каждой серии — либо `часть 1 ✔ можно монтировать`, либо сколько клипов ей ещё не хватает: **Завершить и продолжить** требует одной готовой серии, а не всех. Продолжать при этом ничто не мешает — пока часть рендерится, генерируй дальше и клади в `manual/inbox/`, следующий резюм их подберёт. Если хочется, чтобы дорама вышла целиком разом — или чтобы сначала посмотреть всё, а потом уже публиковать, — выключи тумблер **Доводить части по одной** (`--parts-at-once`), и ничего не смонтируется, пока не приедет последний клип: ровно как было раньше.

- **Цензура и кадрирование**: два переключателя, чтобы прогон дожил до публикации. **Ограничения картинки** (`--visual-notes`) связывают только то, что показывают, и больше ничего — сюжет пишется так, будто их нет, поэтому «всё оружие игрушечное» оставляет перестрелку перестрелкой и меняет лишь реквизит; они уходят сценаристу и дописываются к каждому промпту. **Чистые субтитры** (`--clean-subs`) заменяют мат в вожжённом тексте, оставляя его в озвучке — платформы модерируют то, что могут прочитать. Переписываются целые реплики, а не отдельные слова: «Съебал нахуй с моей пары пидорас блять» → «Уйдите пожалуйста с моей пары молодой человек» — пословная замена оставила бы хромающее предложение. Ловит и слова, которые лишь похожи на мат, вроде первой части имени «Хуй Сунь Вынь», а поскольку переписывание меняет количество слов, интервал реплики заново делится между новыми словами, чтобы она по-прежнему начиналась и кончалась вместе с речью. Расход ограничен: сначала реплики просеиваются регуляркой, поэтому на чистом видео запроса не будет вовсе, а уходят только помеченные (каждая с ближайшими соседями для контекста) одним запросом на видео. Ответ размечен номерами строк, так что частичный ответ доносит то, что вернул, а маскируются лишь остальные.

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

- `slopgen.toml` — глобальный (видео, целевая длительность, сабы, музыка, активный LLM-профиль, порядок провайдеров футажа, язык/тема интерфейса) и `[tts.pronounce.<язык>]` (см. ниже);
- `content/*.toml` — типы контента: брифы промптов по языкам, голоса edge-tts, fallback-ключевые слова;
- `ads/*.toml` — рекламные контракты: ссылка, секция overlay (ассеты, подпись, позиция, тайминг), секция native (ассеты, talking points для вплетения в озвучку), сниппет для описания;
- `accounts/*.toml` — площадки публикации + их дефолты;
- `presets/*.toml` — бандлы параметров для запуска одной командой.
- `characters/*.toml` — каст ИИ-дорамы (`name`, `age`, `appearance`, компилируемый `visual_prompt`).
- `orchestration/*.toml` — цепочки ИИ-генераторов для дорамы (упорядоченные `[[stages]]` с `model`/`key_mode`/`key`/`metric`/`amount` и необязательным `clip_seconds` на этап).
- `visuals/*.toml` — профили видеоряда: источник фона, привязка, ИИ-модель, интервал, движение, непрерывный режим, передние вставки — описаны ниже.
- `llm/*.toml` — подключения к нейронкам (`provider`, `model`, `key_env`, `temperature`, `web_search`); активное называется в `slopgen.toml` `[llm].profile`.

## Ассеты (`assets/`)

`ads/<контракт>/overlay/` — угловые анимации (.webm с альфой, .gif, .png); `ads/<контракт>/native/` — готовые рекламные вставки; `music/` — фоновые треки (берётся случайный, тихо подмешивается); `fonts/` — шрифты сабов; `footage/` — локальные клипы для провайдера `local`; `footage/gameplay/` — фоновые лупы для профиля `gameplay`; `images/` — локальные картинки для фото-фона и вставок. Текущие демо-файлы — заглушки для теста, замени их настоящими.

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
