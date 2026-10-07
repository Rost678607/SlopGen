"""Taking named things off this machine and putting them on another one.

The thing this replaces is a hand-packed zip. Somebody wants to send a friend a world
and the voices that narrate it, so they go into `configs/`, guess which folders matter,
`zip -r` the lot, and the friend unpacks it over their own `configs/` — which is the
same gesture as `cp -r`, and does the same damage: a preset that happened to share a
name is gone, a voice card arrives without the audio it names, and nothing anywhere says
which of the two happened. The zip that prompted this held `configs/fandoms`,
`configs/voices` and `assets/music`, 155 MiB, assembled by eye.

So three ideas, in the order they matter:

**A thing is what it needs.** A preset alone is four dangling names — its content type,
its ad, its visuals profile, its push account — and a fandom without its shapes is a
world whose inhabitants have no bodies. A voice card without the `ref` audio beside it
clones nothing at all, and an effect without its picture is a row in a table. So the
unit of sharing is not a file, it is a NAMED THING plus the files it owns plus the names
it points at, and `closure()` is what turns four tickboxes into a bundle that works on
arrival. `REFS` and `ASSET_REFS` are where that graph is written down, and they are
tables rather than code because the alternative is this module knowing every model.

**Arriving must not destroy.** Default on a collision is `beside`: the newcomer is
written under a free name and everything in the same bundle that pointed at it is
repointed to the new one, so the import is additive and the result still runs. That last
clause is the whole engineering: renaming a thing and leaving its referrers pointing at
the old name produces a bundle that unpacks cleanly and then fails at the first stage
that looks something up. `overwrite` exists, `skip` exists, and neither is the default.

**Identical is not a conflict.** Most of what a second import carries is what the first
one carried. A thing whose every file is byte-for-byte what is already on disk is
reported as `same` and advised `skip`, because copying it in beside itself is how a
config folder fills up with "Плёнка (2)", "Плёнка (3)" that are all the same filter.

A bundle is a zip: `slopgen.json` at the root, and every file under `files/` at the path
it lives at in the project. The manifest is read without unpacking, so `peek()` and
`plan()` can tell the operator what is in a stranger's bundle and exactly what it would
do to their machine before a single byte lands.

Deliberately NOT in here: runs under `output/` and `configs/slopgen.toml`. A run is a
record of one video rather than a setting, carries its own absolute paths, and is the
one thing in the project measured in gigabytes; the global config is where the API keys
live, and a share feature that mails someone's keys to a friend is a bug with a UI.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
import tomllib
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import tomli_w

from .config import loader

MANIFEST = "slopgen.json"  # the manifest, at the root of the bundle
PAYLOAD = "files"  # everything else, under here, at its path in the project
FORMAT = "slopgen-bundle"
VERSION = 1

# What kind of thing can be shared. The flat kinds are one TOML each, named by the file
# stem (checked: of the 43 on this machine, none has a `name` in the body that disagrees
# with its stem — `write_config` drops the field for exactly that reason). `fandoms` is
# the one kind that is a FOLDER, because a world's lore documents, its cast and its frame
# base live next to its TOML, and all of it is the thing.
FLAT_KINDS = ("presets", "content", "ads", "accounts", "visuals", "llm", "characters",
              "voices", "orchestration", "shapes", "chat", "personas", "effects",
              "voicefx")
DIR_KINDS = ("fandoms",)

# The kinds whose item owns FILES BESIDE IT in the shared folder. `_load_dir` globs
# `*.toml` only, so the material in there is invisible to the loader — which is what
# makes the folder work as a base, and what makes this table necessary: without it a
# voice travels as a card with no audio. A `*` walks a dict's values.
MATERIAL: dict[str, tuple[str, ...]] = {
    "voices": ("samples.*.ref",),
    "effects": ("file", "sound"),
    "voicefx": ("bed",),
}

# Where a name of one kind is written down in a config of another. The preset is mostly
# references, which is why it is most of this table. A `*` walks a list's items or a
# dict's values: a content type names a voice per language, a chat names its cast.
REFS: dict[str, tuple[tuple[str, str], ...]] = {
    "presets": (("content_type", "content"), ("ad", "ads"),
                ("visuals", "visuals"), ("push", "accounts")),
    "fandoms": (("shapes", "shapes"),),
    "personas": (("voice", "voices"),),
    "content": (("voices.*", "voices"),),
    "chat": (("cast.*", "personas"),),
}

# …and where one names a FILE under `assets/`. These are not config references and
# cannot be renamed on arrival the way a config name can — a persona's avatar is a file
# name, so the file has to keep it. Which means an avatar that collides is a genuine
# either/or, and `beside` cannot save it; see `_asset_plan`.
ASSET_REFS: dict[str, tuple[tuple[str, str], ...]] = {
    "personas": (("avatar", "avatars"),),
    "chat": (("header_avatar", "avatars"), ("background", "chat_bg"),
             ("sfx", "chat_sfx"), ("split_clip", "footage")),
}

# The asset shelves, as selectable things in their own right. `exports` and `fonts` are
# left out on purpose: the first is where finished videos land, and the second is a
# system font the shell supplies rather than anything the operator made.
ASSET_BUCKETS = ("music", "footage", "images", "avatars", "chat_bg", "chat_sfx", "ads")

ASSET = "assets"  # the pseudo-kind; the name is the path inside `assets/`

# What this machine has already taken in, so that importing the same bundle twice is
# quiet. Content alone cannot answer that question: the first import RENAMES things and
# repoints the references inside them, so what is on disk is deliberately no longer
# byte-for-byte what the bundle holds, and asking only about content makes «Разбор (3)»
# on the second run and «Разбор (4)» on the third. The ledger remembers what arrived and
# what it ended up being called, which is the one fact neither names nor bytes retain.
LEDGER = Path("state") / "imported.json"


class ShareError(Exception):
    pass


Ref = tuple[str, str]  # (kind, name) — a thing's identity anywhere in here


@dataclass(frozen=True)
class Item:
    """One shareable thing: what it is called, what files are ITS, what it points at."""

    kind: str
    name: str
    files: tuple[str, ...]  # project-relative, forward slashes
    needs: tuple[Ref, ...] = ()
    note: str = ""  # a line for the picker, from the config's own description

    @property
    def ref(self) -> Ref:
        return (self.kind, self.name)

    @property
    def bytes(self) -> int:
        return sum(Path(f).stat().st_size for f in self.files if Path(f).is_file())


@dataclass
class Verdict:
    """What one incoming item would do to this machine, before anything is done."""

    ref: Ref
    state: str  # "new" | "same" | "differs"
    advise: str  # the default decision: "take" | "skip" | "beside"
    files: tuple[str, ...]  # where it would land as it stands
    clash: tuple[str, ...] = ()  # the local files it would overwrite
    note: str = ""
    held_by: str = ""  # when `same`: the local name this machine keeps it under


@dataclass
class Report:
    took: list[Ref] = field(default_factory=list)
    renamed: dict[str, str] = field(default_factory=dict)  # "kind/old" -> new name
    skipped: list[Ref] = field(default_factory=list)
    replaced: list[Ref] = field(default_factory=list)
    wrote: list[str] = field(default_factory=list)
    repointed: list[str] = field(default_factory=list)  # "kind/name: field old -> new"


# ---------------------------------------------------------------- reading a value out
#
# One tiny path walker rather than a model visitor, because the tables above are the
# point: a field that names something is one line of data, not a method somewhere.


def _dig(data, path: str) -> list:
    """Every value at a dotted path, where `*` walks a list or a dict's values."""
    cur = [data]
    for step in path.split("."):
        nxt = []
        for node in cur:
            if step == "*":
                if isinstance(node, dict):
                    nxt.extend(node.values())
                elif isinstance(node, list):
                    nxt.extend(node)
            elif isinstance(node, dict) and step in node:
                nxt.append(node[step])
        cur = nxt
    return [c for c in cur if c not in (None, "")]


def _poke(data, path: str, old, new) -> bool:
    """Replace `old` with `new` at a dotted path. Returns whether anything moved."""
    steps = path.split(".")
    last = steps[-1]
    cur = [data]
    for step in steps[:-1]:
        nxt = []
        for node in cur:
            if step == "*":
                if isinstance(node, dict):
                    nxt.extend(node.values())
                elif isinstance(node, list):
                    nxt.extend(node)
            elif isinstance(node, dict) and step in node:
                nxt.append(node[step])
        cur = nxt
    hit = False
    for node in cur:
        if last == "*":
            # the leaf is itself the walked collection: a chat's `cast`, a content
            # type's `voices` — the names sit in the list or in the dict's values
            if isinstance(node, list):
                for i, v in enumerate(node):
                    if v == old:
                        node[i] = new
                        hit = True
            elif isinstance(node, dict):
                for k, v in node.items():
                    if v == old:
                        node[k] = new
                        hit = True
        elif isinstance(node, dict) and node.get(last) == old:
            node[last] = new
            hit = True
    return hit


def _read(path: Path) -> dict:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ShareError(f"{path}: {e}") from e


def _rel(p: Path) -> str:
    return p.as_posix()


# ---------------------------------------------------------------- what is on this machine


def _under(d: Path, assets: Path) -> list[Ref]:
    """Every asset in a folder a config points at, as refs. An ad's material is named
    as a DIRECTORY (`assets/ads/B0xi/overlay`) rather than file by file, and a chat's
    `split_clip` may be a shelf spelled with a trailing slash — so the folder has to be
    expanded here or an ad travels as a contract with no pictures."""
    if not d.is_dir():
        return []
    out = []
    for q in sorted(d.rglob("*")):
        if q.is_file() and q.name != ".gitkeep":
            try:
                out.append((ASSET, _rel(q.relative_to(assets))))
            except ValueError:  # somewhere else entirely; not ours to carry
                pass
    return out


def _flat(kind: str, root: Path, assets: Path) -> list[Item]:
    d = root / kind
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.glob("*.toml")):
        body = _read(p)
        files = [_rel(p)]
        # the material beside the card, named by the card
        for spec in MATERIAL.get(kind, ()):
            for val in _dig(body, spec):
                beside = d / str(val)
                if beside.is_file():
                    files.append(_rel(beside))
        needs: list[Ref] = []
        for fld, target in REFS.get(kind, ()):
            needs += [(target, str(v)) for v in _dig(body, fld)]
        for fld, bucket in ASSET_REFS.get(kind, ()):
            for v in _dig(body, fld):
                val = str(v)
                if val.endswith("/"):  # a shelf, not a file
                    needs += _under(assets / bucket / val, assets)
                else:
                    needs.append((ASSET, f"{bucket}/{val}"))
        # an ad's material is a whole folder, and it is spelled as a path from the
        # PROJECT root rather than from the assets root the way every other reference is
        for fld in ("overlay.assets_dir", "native.assets_dir"):
            for v in _dig(body, fld):
                needs += _under(Path(str(v)), assets)
        # a description is a line for the picker only when it IS a line: an ad's is a
        # table of fields, and `str()` of it is a Python dict printed at the operator
        note = body.get("description", "")
        out.append(Item(kind, p.stem, tuple(files), tuple(dict.fromkeys(needs)),
                        note[:120] if isinstance(note, str) else ""))
    return out


def _fandoms(root: Path) -> list[Item]:
    d = root / "fandoms"
    if not d.is_dir():
        return []
    out = []
    for folder in sorted(x for x in d.iterdir() if x.is_dir()):
        files = tuple(_rel(p) for p in sorted(folder.rglob("*")) if p.is_file())
        toml = folder / loader.FANDOM_TOML
        body = _read(toml) if toml.is_file() else {}
        needs = [("shapes", str(v)) for v in _dig(body, "shapes")]
        out.append(Item("fandoms", folder.name, files, tuple(needs),
                        str(body.get("tone", ""))[:120]))
    return out


def _assets(assets: Path) -> list[Item]:
    out = []
    for bucket in ASSET_BUCKETS:
        d = assets / bucket
        if not d.is_dir():
            continue
        for p in sorted(x for x in d.rglob("*") if x.is_file()):
            if p.name == ".gitkeep":
                continue
            out.append(Item(ASSET, _rel(p.relative_to(assets)), (_rel(p),)))
    return out


def survey(root: Path | None = None, assets: Path | None = None) -> list[Item]:
    """Everything on this machine that can be handed to somebody else."""
    root = root or loader.CONFIGS_DIR
    assets = assets or Path(ASSET)
    out: list[Item] = []
    for kind in FLAT_KINDS:
        out += _flat(kind, root, assets)
    out += _fandoms(root)
    out += _assets(assets)
    return out


def closure(items: list[Item], picked: list[Ref]) -> list[Ref]:
    """The refs the picked things need and nobody picked. Walked to a fixed point,
    because a preset needs a content type which needs a voice which has audio."""
    have = {i.ref: i for i in items}
    seen = set(picked)
    queue = list(picked)
    extra: list[Ref] = []
    while queue:
        it = have.get(queue.pop())
        if it is None:
            continue
        for need in it.needs:
            if need in seen:
                continue
            seen.add(need)
            # a name that is nothing on this machine is not a missing file: a content
            # type naming an edge-tts voice points at a service, not at a card
            if need in have:
                extra.append(need)
                queue.append(need)
    return extra


# ---------------------------------------------------------------- writing a bundle


# 1980-01-01, the earliest a zip entry can claim. Material that came off a download or
# out of somebody else's archive can carry an mtime of zero, and `ZipFile.write` raises
# on it rather than rounding — which is a world refusing to pack over a date nobody will
# ever read. So the entry is built by hand and the clock is clamped.
ZIP_DAWN = (1980, 1, 1, 0, 0, 0)


def _zinfo(p: Path, arc: str) -> zipfile.ZipInfo:
    st = p.stat()
    t = time.localtime(st.st_mtime)
    zi = zipfile.ZipInfo(arc, date_time=t[:6] if t.tm_year >= 1980 else ZIP_DAWN)
    zi.compress_type = zipfile.ZIP_DEFLATED
    zi.external_attr = (st.st_mode & 0xFFFF) << 16
    return zi


def pack(dest: Path, items: list[Item], picked: list[Ref], *, note: str = "") -> dict:
    """Write the picked things, and nothing else, into a bundle at `dest`."""
    have = {i.ref: i for i in items}
    chosen = [have[r] for r in dict.fromkeys(picked) if r in have]
    if not chosen:
        raise ShareError("nothing to pack")
    man = {
        "format": FORMAT,
        "version": VERSION,
        "made_at": int(time.time()),
        "note": note,
        "items": [],
    }
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for it in chosen:
                rows = []
                for f in it.files:
                    p = Path(f)
                    if not p.is_file():
                        continue
                    with z.open(_zinfo(p, f"{PAYLOAD}/{f}"), "w") as out, open(p, "rb") as fh:
                        shutil.copyfileobj(fh, out)
                    rows.append({"path": f, "sha": loader.file_sha(p),
                                 "size": p.stat().st_size})
                man["items"].append({
                    "kind": it.kind, "name": it.name, "note": it.note,
                    "needs": [list(n) for n in it.needs], "files": rows,
                })
            z.writestr(MANIFEST, json.dumps(man, ensure_ascii=False, indent=1))
    except BaseException:
        tmp.unlink(missing_ok=True)  # a half-written bundle is worse than none
        raise
    tmp.replace(dest)
    return man


def peek(src: Path) -> dict:
    """The manifest, without unpacking anything. The first thing anyone does with a
    bundle somebody else made, and it must not require trusting it."""
    try:
        with zipfile.ZipFile(src) as z:
            man = json.loads(z.read(MANIFEST))
    except (OSError, KeyError, zipfile.BadZipFile, json.JSONDecodeError) as e:
        raise ShareError(f"not a slopgen bundle: {e}") from e
    if man.get("format") != FORMAT:
        raise ShareError(f"not a slopgen bundle: {man.get('format')!r}")
    if int(man.get("version", 0)) > VERSION:
        raise ShareError(f"bundle version {man['version']} is newer than this slopgen")
    for row in man.get("items", []):
        for f in row.get("files", []):
            # a bundle is a stranger's file, and a path in it is a path somebody else
            # chose: nothing may land outside the two folders this feature owns
            p = f.get("path", "")
            if p.startswith("/") or ".." in Path(p).parts:
                raise ShareError(f"bundle wants to write outside the project: {p!r}")
            top = Path(p).parts[0] if Path(p).parts else ""
            if top not in (loader.CONFIGS_DIR.name, ASSET):
                raise ShareError(f"bundle wants to write to {top!r}, which it may not")
    return man


# ---------------------------------------------------------------- what it would do here


def _print(files: dict[str, str]) -> str:
    """A thing's content, independent of what it is called."""
    h = hashlib.sha256()
    for path, sha in sorted(files.items()):
        h.update(path.encode() + b"\0" + sha.encode() + b"\0")
    return h.hexdigest()[:16]


def _ledger(path: Path | None = None) -> dict[str, str]:
    """`{kind/name@content: the name it is here under}` — what has already arrived."""
    p = path or LEDGER
    try:
        got = json.loads(p.read_text(encoding="utf-8"))
        return got if isinstance(got, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _remember(entries: dict[str, str], path: Path | None = None) -> None:
    p = path or LEDGER
    if not entries:
        return
    was = _ledger(p)
    was.update(entries)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(was, ensure_ascii=False, indent=1), encoding="utf-8")


def _stamp(kind: str, name: str, content: str) -> str:
    return f"{kind}/{name}@{content}"


def plan(man: dict, items: list[Item] | None = None, *,
         ledger: Path | None = None) -> list[Verdict]:
    """Per incoming thing: is it new, is it exactly what is here already, or does it
    differ? Answered by content and not by date, because `shutil.copy2` keeps an mtime
    and a restored file is older than the thing it replaced.

    By content and not by NAME either, which is the subtler half. Importing the same
    bundle twice has to be quiet, and after the first import the newcomer is sitting
    there under «Утро (2)» — still differing from the «Утро» it was renamed around. Ask
    only whether the name is taken and the second import makes «Утро (3)», the third
    makes «Утро (4)», and the feature that exists to stop a config folder filling up
    with near-duplicates is the thing filling it up. So a thing this machine already
    holds under ANY name is `same`, and the verdict says which name that is."""
    local = items if items is not None else survey()
    here = {i.ref: i for i in local}
    byprint: dict[str, dict[str, str]] = {}
    for i in local:
        shelf = byprint.setdefault(i.kind, {})
        shelf.setdefault(_print({_within(f, i.name): loader.file_sha(Path(f))
                                 for f in i.files if Path(f).is_file()}), i.name)
    seen = _ledger(ledger)
    out = []
    for row in man.get("items", []):
        ref: Ref = (row["kind"], row["name"])
        incoming = tuple(f["path"] for f in row.get("files", []))
        theirs = _print({_within(f["path"], row["name"]): f["sha"]
                         for f in row.get("files", [])})
        holder = byprint.get(row["kind"], {}).get(theirs)
        # …or something this machine took in once already and has since renamed and
        # repointed, which is why the ledger is asked before the bytes are
        was = seen.get(_stamp(row["kind"], row["name"], theirs))
        if holder is None and was is not None and (row["kind"], was) in here:
            holder = was
        if holder is not None:
            mine = here.get((row["kind"], holder))
            out.append(Verdict(ref, "same", "skip", incoming,
                               mine.files if mine else (), row.get("note", ""), holder))
        elif ref in here:
            out.append(Verdict(ref, "differs", "beside", incoming,
                               here[ref].files, row.get("note", "")))
        else:
            out.append(Verdict(ref, "new", "take", incoming, (), row.get("note", "")))
    return out


def _within(path: str, name: str) -> str:
    """A file's path WITHIN the thing that owns it, so two things can be compared by
    content. Not the basename: a fandom holds `characters/x.toml` and `frames/x.toml`,
    and keying by basename would call one the other and miss a difference."""
    parts = list(Path(path).parts)
    for i, part in enumerate(parts):
        if part == name or Path(part).stem == name:
            return Path(*parts[i + 1:]).as_posix() or "."
    return Path(path).as_posix()


def _free(kind: str, name: str, taken: set[str]) -> str:
    """A name of this kind that nothing holds — neither on disk nor in this import.

    An asset's name is its path under `assets/`, so the number goes on the STEM and the
    extension stays where a player can see it: `утро.m4a` arrives beside as
    `утро (2).m4a`, never as `утро.m4a (2)`."""
    if kind == ASSET:
        q = PurePosixPath(name)
        n = 2
        while str(q.with_name(f"{q.stem} ({n}){q.suffix}")) in taken:
            n += 1
        return str(q.with_name(f"{q.stem} ({n}){q.suffix}"))
    n = 2
    while f"{name} ({n})" in taken:
        n += 1
    return f"{name} ({n})"


# ---------------------------------------------------------------- putting it on here


def _retarget(path: str, old: str, new: str) -> str:
    """The same file path with the item's own name swapped in its stem or its folder."""
    p = Path(path)
    parts = list(p.parts)
    for i, part in enumerate(parts):
        if part == old:  # a fandom: the folder names it
            parts[i] = new
            return Path(*parts).as_posix()
        if Path(part).stem == old:  # a flat kind: the stem names it
            parts[i] = new + Path(part).suffix
            return Path(*parts).as_posix()
    return path


def apply(src: Path, man: dict, choices: dict[Ref, str], *,
          items: list[Item] | None = None, ledger: Path | None = None) -> Report:
    """Carry out the plan. `choices` maps a thing to "take", "skip", "beside" or
    "overwrite"; anything not mentioned is skipped, so a caller that asks for nothing
    does nothing.

    The order is: decide every final name FIRST, then write. That is not tidiness — a
    thing renamed on arrival has to be repointed in everything that travelled with it,
    and that cannot be known while the names are still being chosen.
    """
    rep = Report()
    got: dict[str, str] = {}
    here = {i.ref: i for i in (items if items is not None else survey())}
    rows = {(r["kind"], r["name"]): r for r in man.get("items", [])}

    # --- 1. the final name of everything that is coming
    taken: dict[str, set[str]] = {}
    for kind, name in list(here) + list(rows):
        taken.setdefault(kind, set()).add(name)
    final: dict[Ref, str] = {}
    for ref, how in choices.items():
        if how == "skip" or ref not in rows:
            continue
        kind, name = ref
        if how == "beside" and ref in here:
            new = _free(kind, name, taken[kind])
            taken[kind].add(new)
            final[ref] = new
            rep.renamed[f"{kind}/{name}"] = new
        else:
            final[ref] = name

    # --- 2. write, repointing as we go
    with zipfile.ZipFile(src) as z:
        for ref, how in choices.items():
            if ref not in final:
                rep.skipped.append(ref)
                continue
            row = rows[ref]
            kind, name = ref
            new = final[ref]
            if how == "overwrite" and ref in here:
                rep.replaced.append(ref)
            rename = new != name
            for f in row.get("files", []):
                srcp = f["path"]
                # an asset's name IS its path under `assets/`, so a renamed one lands
                # where its new name says rather than where it came from — otherwise
                # `beside` writes straight over the file it was supposed to spare
                if rename and kind == ASSET:
                    dstp = f"{ASSET}/{new}"
                else:
                    dstp = _retarget(srcp, name, new) if rename else srcp
                dst = Path(dstp)
                if dst.exists() and how not in ("overwrite", "beside", "take"):
                    continue
                dst.parent.mkdir(parents=True, exist_ok=True)
                with z.open(f"{PAYLOAD}/{srcp}") as fh, open(dst, "wb") as out:
                    shutil.copyfileobj(fh, out)
                rep.wrote.append(dstp)
                if dst.suffix == ".toml":
                    _patch(dst, kind, new if rename else None, final, rep,
                           label=new if kind in DIR_KINDS else None)
            rep.took.append((kind, new))
            got[_stamp(kind, name, _print({_within(f["path"], name): f["sha"]
                                           for f in row.get("files", [])}))] = new
    _remember(got, ledger)
    return rep


def _patch(path: Path, kind: str, rename: str | None, final: dict[Ref, str],
           rep: Report, *, label: str | None = None) -> None:
    """Rewrite a just-written TOML so it still says the truth: its own new name, and
    the new names of anything it points at that was renamed on the way in.

    Only rewritten when something actually changed. A fandom's TOML is a file somebody
    hand-edited with comments in it, and `tomli_w` would hand back a tidied file with
    the comments gone — so an untouched config is left as the bytes that arrived."""
    moved = {ref: new for ref, new in final.items() if new != ref[1]}
    if rename is None and not moved:
        return
    body = _read(path)
    hit = False
    if rename is not None and "name" in body:
        body["name"] = rename
        hit = True
    who = label or rename or path.stem
    for fld, target in REFS.get(kind, ()):
        for (k, old), new in moved.items():
            if k == target and _poke(body, fld, old, new):
                hit = True
                rep.repointed.append(f"{kind}/{who}: {fld} {old} -> {new}")
    # …and the files under `assets/`, which are named relative to their shelf: a
    # persona's `avatar` says `host.png`, while the asset's name here is
    # `avatars/host.png`. Repointed for the same reason a config name is — a renamed
    # avatar that nobody is pointed at is a file, not a face.
    for fld, bucket in ASSET_REFS.get(kind, ()):
        for (k, old), new in moved.items():
            if k != ASSET:
                continue
            head = f"{bucket}/"
            if old.startswith(head) and new.startswith(head) and \
                    _poke(body, fld, old[len(head):], new[len(head):]):
                hit = True
                rep.repointed.append(f"{kind}/{who}: {fld} {old} -> {new}")
    if hit:
        path.write_bytes(tomli_w.dumps(body).encode())
