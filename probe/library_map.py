"""library_map — the target library's own map, read LIVE from its checkout.

The foundry used to carry a copy of where things live in the library: an `area →
directory` table, one benchmark path, and whatever module paths an issue body spelled.
On 2026-11-08 the flagship reorganised into layers (`Models/`, `Risk/`, `Choice/`, …) and
split its corpus into one file per area, and every one of those copies went dead at
once: not one pointer in the 54 ready issues named a file that still existed, the area
table mapped to directories that were gone, and the benchmark file had been deleted.
Nothing failed loudly, because each consumer treated a missing path as "no pointer".

So nothing here is a copy. A pack declares WHERE the library keeps its own map
(`[layout]` in `target.toml`: the taxonomy, the bibliography, the corpus directory, the
blueprint) and this module reads it at tick time:

* `heal_pointers` — a path that moved is found again by its basename;
* `Taxonomy` — an issue's `area:` label names a cell, a cell names its directories and
  its subject codes;
* `benchmark_for` — a new entry goes in the corpus file its neighbours are in;
* `Bibliography` — the keys a module docstring and a corpus entry may cite;
* `Blueprint` — whether the node an issue names is proved or still on the frontier, and
  the corpus entries already recorded as proving part of it.

Every field of the layout is optional: a pack that declares none of it (a library with no
taxonomy yet) gets the pack's own static answers, exactly as before.

Stdlib only, and domain-free like the rest of `probe/`.
"""
from __future__ import annotations

import json
import os
import re
import textwrap
import tomllib
from collections import Counter
from dataclasses import dataclass, field

__all__ = ["Layout", "heal_pointers", "Taxonomy", "benchmark_for", "Bibliography",
           "Blueprint", "named_nodes", "served_issues", "corpus_ids", "declared_in_library",
           "entry_descriptions", "pointer_directory", "module_doc", "add_core",
           "long_lines", "LINE_LIMIT", "price_table"]

#: The library's line limit (Mathlib's `linter.style.longLine`); its ratchet counts every
#: longer line in a source file, with the baseline at zero.
LINE_LIMIT = 100


@dataclass(frozen=True)
class Layout:
    """Where the target library keeps its own map, repo-relative. Empty = not declared."""

    taxonomy: str = ""
    bibliography: str = ""
    benchmarks_dir: str = ""
    blueprint_nodes: str = ""
    blueprint_frontier: str = ""
    #: the module every library module imports, directly or through its imports
    init_module: str = ""
    #: emit the module docstring in the library's documentation standard
    doc_standard: bool = False
    #: the tool that narrows a module's imports after it is built (`lake exe <name>`)
    shake_exe: str = ""
    #: the library's table of its pricing functional and of the definitions that claim to
    #: be prices (`[trunk] anchors`, `[prices] names`): an entry that reaches a price but
    #: not the functional is a ratchet count the library's build refuses to raise
    price_table: str = ""

    @classmethod
    def from_mapping(cls, raw) -> "Layout":
        raw = dict(raw or {})
        return cls(**{k: raw[k] for k in cls.__dataclass_fields__ if k in raw})


def price_table(main_repo: str, path: str) -> dict | None:
    """`{"trunk": [...], "prices": [...]}` from the library's price table, or None when the
    pack declares none or the file is missing or malformed (the trunk check then skips)."""
    if not path:
        return None
    try:
        with open(os.path.join(main_repo, path), "rb") as f:
            raw = tomllib.load(f)
        trunk = [str(x) for x in raw["trunk"]["anchors"]]
        prices = [str(x) for x in raw["prices"]["names"]]
    except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError):
        return None
    return {"trunk": trunk, "prices": prices} if trunk and prices else None


# --- pointers -----------------------------------------------------------------


def _basename_index(main_repo: str, lake_root: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    root = os.path.join(main_repo, lake_root)
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            if fn.endswith(".lean"):
                rel = os.path.relpath(os.path.join(dirpath, fn), main_repo)
                out.setdefault(fn, []).append(rel.replace(os.sep, "/"))
    return out


def heal_pointers(main_repo: str, lake_root: str,
                  pointers: list[str]) -> tuple[list[str], list[dict]]:
    """Resolve each pointer against the checkout. A path that exists is kept. One that
    does not is looked up by BASENAME: a module that moved keeps its file name, so a
    unique match is the same module at its new address. Returns `(live, notes)`; a note
    is `{pointer, how, to}` with `how` one of `moved` (healed), `ambiguous` (several
    modules share the name — dropped rather than guessed) or `missing` (dropped: it names
    a module to create, or one that is gone)."""
    index: dict[str, list[str]] | None = None
    live: list[str] = []
    notes: list[dict] = []
    for p in pointers:
        if os.path.isfile(os.path.join(main_repo, p)):
            resolved = p
        else:
            if index is None:
                index = _basename_index(main_repo, lake_root)
            hits = index.get(os.path.basename(p), [])
            if len(hits) == 1:
                resolved = hits[0]
                notes.append({"pointer": p, "how": "moved", "to": resolved})
            else:
                notes.append({"pointer": p, "how": "ambiguous" if hits else "missing",
                              "to": sorted(hits)})
                continue
        if resolved not in live:
            live.append(resolved)
    return live, notes


# --- taxonomy -----------------------------------------------------------------


@dataclass(frozen=True)
class Taxonomy:
    """The library's cells: `key` → the module-name prefixes that belong to it and its
    subject codes. A module belongs to the cell of its LONGEST matching prefix."""

    cells: tuple[dict, ...] = ()

    @classmethod
    def load(cls, main_repo: str, rel: str) -> "Taxonomy":
        if not rel:
            return cls()
        try:
            with open(os.path.join(main_repo, rel), "rb") as f:
                data = tomllib.load(f)
        except (OSError, tomllib.TOMLDecodeError):
            return cls()
        return cls(tuple(c for c in data.get("cell", []) if isinstance(c, dict)))

    def __bool__(self) -> bool:
        return bool(self.cells)

    def cell(self, key: str) -> dict | None:
        return next((c for c in self.cells if c.get("key") == key), None)

    def cell_of_module(self, module: str) -> dict | None:
        best, best_len = None, -1
        for c in self.cells:
            for prefix in c.get("modules", []) or []:
                if (module == prefix or module.startswith(prefix + ".")) and len(prefix) > best_len:
                    best, best_len = c, len(prefix)
        return best

    def subjects_of_module(self, module: str) -> list[str]:
        c = self.cell_of_module(module)
        return list(c.get("subjects", []) or []) if c else []

    def directory_for(self, main_repo: str, areas: list[str]) -> str | None:
        """The repo-relative directory a result in one of `areas` (issue `area:` labels,
        most specific first) belongs in: the first prefix of the first matching cell that
        is a directory in the checkout, else the directory of its first prefix that is a
        module (a cell listed module by module, like the risk measures, lives where those
        modules do). None when no label names a cell with either."""
        for area in areas:
            c = self.cell(area)
            prefixes = [p.replace(".", "/") for p in (c.get("modules", []) or [])] if c else []
            for rel in prefixes:
                if os.path.isdir(os.path.join(main_repo, rel)):
                    return rel
            for rel in prefixes:
                if os.path.isfile(os.path.join(main_repo, rel + ".lean")) and "/" in rel:
                    return os.path.dirname(rel)
        return None


def pointer_directory(pointers: list[str]) -> str | None:
    """The directory most of the pointers live in — where a result that consumes them
    sits when no taxonomy cell says otherwise."""
    dirs = Counter(os.path.dirname(p) for p in pointers if "/" in p)
    return dirs.most_common(1)[0][0] if dirs else None


# --- corpus -------------------------------------------------------------------

_IMPORT_LINE = re.compile(r"(?m)^\s*(?:public\s+)?import\s+(\S+)")


def _entries(path: str) -> list[dict]:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    rows = data.get("theorems", []) if isinstance(data, dict) else data
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _corpus_files(main_repo: str, benchmarks_dir: str) -> list[str]:
    base = os.path.join(main_repo, benchmarks_dir)
    try:
        names = sorted(n for n in os.listdir(base) if n.endswith(".json"))
    except OSError:
        return []
    return [f"{benchmarks_dir}/{n}" for n in names]


def benchmark_for(main_repo: str, benchmarks_dir: str, directory: str,
                  fallback: str = "") -> str:
    """The corpus file a new entry about a module in `directory` belongs in: the file
    most of that directory's existing entries are in (an entry is "about" the modules its
    snippet imports). Walks up one directory at a time when the directory has no entries
    yet; `fallback` when nothing matches or the layout declares no corpus directory."""
    files = _corpus_files(main_repo, benchmarks_dir) if benchmarks_dir else []
    if not files:
        return fallback
    imports = {f: [m for e in _entries(os.path.join(main_repo, f))
                   for m in _IMPORT_LINE.findall((e.get("code") or {}).get("lean") or "")]
               for f in files}
    prefix = directory.strip("/").replace("/", ".")
    while prefix:
        votes = Counter({f: sum(m == prefix or m.startswith(prefix + ".") for m in mods)
                         for f, mods in imports.items()})
        best = [f for f, n in votes.items() if n and n == max(votes.values())]
        if best:
            return sorted(best)[0]
        if "." not in prefix:
            break
        prefix = prefix.rsplit(".", 1)[0]
    if fallback and os.path.isfile(os.path.join(main_repo, fallback)):
        return fallback
    return max(files, key=lambda f: (len(imports[f]), f))


def corpus_ids(main_repo: str, benchmarks_dir: str) -> set[str]:
    """Every entry id in the corpus (ids are globally unique there, and gated)."""
    return {str(e["id"]) for f in (_corpus_files(main_repo, benchmarks_dir)
                                   if benchmarks_dir else [])
            for e in _entries(os.path.join(main_repo, f)) if e.get("id")}


def declared_in_library(main_repo: str, lake_root: str, name: str,
                        exclude: tuple[str, ...] = ()) -> str | None:
    """The repo-relative file that already declares `name` (its last component, as a
    theorem / lemma / def / abbrev / structure / inductive / instance), or None.

    A module is drafted importing only its pointers, so a name the library uses in a
    module it does NOT import elaborates cleanly through every gate and then clashes in
    the umbrella build, after the proof has been paid for. It is also the cheapest
    duplicate detector there is: the obvious name for a result that already exists is
    usually the name it already has. `exclude` names repo-relative files to skip (the
    scratch module the draft itself sits in)."""
    short = re.escape(name.rsplit(".", 1)[-1])
    pat = re.compile(rf"(?m)^[ \t]*(?:@\[[^\]]*\][ \t]*)*(?:private[ \t]+|protected[ \t]+|"
                     rf"noncomputable[ \t]+)*(?:theorem|lemma|def|abbrev|structure|inductive|"
                     rf"instance|class)[ \t]+{short}(?![\w.'])")
    for dirpath, _dirs, files in os.walk(os.path.join(main_repo, lake_root)):
        for fn in sorted(files):
            if not fn.endswith(".lean"):
                continue
            path = os.path.join(dirpath, fn)
            rel = os.path.relpath(path, main_repo).replace(os.sep, "/")
            if rel in exclude:
                continue
            try:
                with open(path, encoding="utf-8") as f:
                    if pat.search(f.read()):
                        return rel
            except OSError:
                continue
    return None


def served_issues(main_repo: str, benchmarks_dir: str) -> set[int]:
    """Issue numbers the corpus already holds a pipeline contribution for.

    An issue that stays open after one (a subset proof `refs` it; a frontier node needs a
    human to say it is delivered) reads `status:ready` again once its PR merges, and the
    refill would draft it a second time. The corpus entry's own provenance is the record
    that it was served, so no private state is needed to see it."""
    out: set[int] = set()
    for f in _corpus_files(main_repo, benchmarks_dir) if benchmarks_dir else []:
        for e in _entries(os.path.join(main_repo, f)):
            prov = (e.get("metadata") or {}).get("provenance")
            if isinstance(prov, dict) and "autoform" in str(prov.get("source") or ""):
                try:
                    out.add(int(prov.get("issue")))
                except (TypeError, ValueError):
                    pass
    return out


# --- bibliography ---------------------------------------------------------------

_BIB_KEY = re.compile(r"^@\w+\s*\{\s*([^,\s]+)\s*,", re.M)
_BIB_FIELD = re.compile(r"(\w+)\s*=\s*\{")
_TEX_ACCENT = re.compile(r"""\\["'`^~=.Hcv]\s?""")


def _tex_plain(value: str) -> str:
    value = _TEX_ACCENT.sub("", value)
    return " ".join(value.replace("{", "").replace("}", "").replace("\\", "").split())


def _surname(author: str) -> str:
    author = author.strip()
    if "," in author:
        return author.split(",", 1)[0].strip()
    return author.rsplit(" ", 1)[-1] if author else ""


@dataclass(frozen=True)
class Bibliography:
    """The library's one bibliography: key → fields (`author`, `title`, `year`, …)."""

    entries: dict = field(default_factory=dict)

    @classmethod
    def load(cls, main_repo: str, rel: str) -> "Bibliography":
        if not rel:
            return cls()
        try:
            with open(os.path.join(main_repo, rel), encoding="utf-8") as f:
                src = f.read()
        except OSError:
            return cls()
        out: dict[str, dict[str, str]] = {}
        for m in _BIB_KEY.finditer(src):
            depth, i = 1, src.index("{", m.start())
            j = i + 1
            while depth and j < len(src):
                depth += {"{": 1, "}": -1}.get(src[j], 0)
                j += 1
            fields: dict[str, str] = {}
            for fm in _BIB_FIELD.finditer(src, m.end(), j - 1):
                depth, v = 1, fm.end()
                while depth and v < j:
                    depth += {"{": 1, "}": -1}.get(src[v], 0)
                    v += 1
                fields[fm.group(1).lower()] = _tex_plain(src[fm.end():v - 1])
            out[m.group(1)] = fields
        return cls(out)

    def __bool__(self) -> bool:
        return bool(self.entries)

    def __contains__(self, key: str) -> bool:
        return key in self.entries

    def authors(self, key: str) -> str:
        names = [_surname(a) for a in re.split(r"\s+and\s+", self.entries[key].get("author", ""))]
        names = [n for n in names if n]
        if len(names) > 2:
            return ", ".join(names[:-1]) + " and " + names[-1]
        return " and ".join(names)

    def title(self, key: str) -> str:
        return self.entries[key].get("title", "") or key

    def reference(self, key: str, locator: str = "") -> str:
        """`Authors, Title (locator)` — a corpus entry's `metadata.reference`."""
        who = self.authors(key)
        base = f"{who}, {self.title(key)}" if who else self.title(key)
        return f"{base}, {locator.strip()}" if locator.strip() else base

    def bullet(self, key: str, locator: str = "") -> str:
        """The docstring form: `* [Authors, *Title*][Key], locator`."""
        who = self.authors(key)
        label = f"{who}, *{self.title(key)}*" if who else f"*{self.title(key)}*"
        tail = f", {locator.strip()}" if locator.strip() else ""
        return f"* [{label}][{key}]{tail}"

    def catalogue(self) -> str:
        """One line per citable work, for the specifier's prompt."""
        rows = []
        for key in sorted(self.entries, key=str.lower):
            f = self.entries[key]
            year = f" ({f['year']})" if f.get("year") else ""
            who = self.authors(key)
            rows.append(f"- {key}: {who + year + ', ' if who else ''}{self.title(key)}")
        return "\n".join(rows)


# --- blueprint ------------------------------------------------------------------

# `Blueprint: a, b` at the start of a line; markdown bold and list bullets tolerated.
_BLUEPRINT_LINE = re.compile(r"^[\s>*-]*\**blueprint\**\s*:\**\s*(.+?)\s*$", re.I | re.M)
_NODE_LABEL = re.compile(r"^(?:thm|def|lem|cor|prop):[a-z0-9][a-z0-9-]*$")


def named_nodes(body: str | None) -> list[str]:
    """The blueprint nodes an issue's `Blueprint:` line names, in order."""
    out: list[str] = []
    for m in _BLUEPRINT_LINE.finditer(body or ""):
        for raw in m.group(1).split(","):
            label = raw.strip().strip("`*")
            if _NODE_LABEL.match(label) and label not in out:
                out.append(label)
    return out


@dataclass(frozen=True)
class Blueprint:
    """The program's nodes: `proved` ones carry a Lean declaration, `frontier` ones are
    stated and not yet formalized (and may list `core` corpus entries proving part)."""

    proved: dict = field(default_factory=dict)
    frontier: dict = field(default_factory=dict)

    @classmethod
    def load(cls, main_repo: str, layout: Layout) -> "Blueprint":
        proved, frontier = {}, {}
        if layout.blueprint_nodes:
            try:
                with open(os.path.join(main_repo, layout.blueprint_nodes), encoding="utf-8") as f:
                    for n in json.load(f):
                        if isinstance(n, dict) and n.get("label"):
                            proved[n["label"]] = n
            except (OSError, ValueError):
                pass
        if layout.blueprint_frontier:
            try:
                with open(os.path.join(main_repo, layout.blueprint_frontier), "rb") as f:
                    for n in tomllib.load(f).get("node", []):
                        if isinstance(n, dict) and n.get("label"):
                            frontier[n["label"]] = n
            except (OSError, tomllib.TOMLDecodeError):
                pass
        return cls(proved, frontier)

    def kind(self, label: str) -> str:
        if label in self.proved:
            return "proved"
        return "frontier" if label in self.frontier else "unknown"

    def frontier_of(self, labels: list[str]) -> list[str]:
        return [x for x in labels if x in self.frontier]

    def hint(self, labels: list[str], limit: int = 40) -> str:
        """The declarations a statement can be built from to rest on the map: first the
        ones the issue's own nodes name or build on, then the map's definition nodes
        (the program's central objects)."""
        names: list[str] = []
        for label in labels:
            if label in self.proved:
                names.append(self.proved[label].get("name", ""))
            for dep in (self.frontier.get(label, {}).get("uses", []) or []):
                if dep in self.proved:
                    names.append(self.proved[dep].get("name", ""))
        names += [n.get("name", "") for lab, n in sorted(self.proved.items())
                  if lab.startswith("def:")]
        out: list[str] = []
        for n in names:
            if n and n not in out:
                out.append(n)
        return ", ".join(f"`{n}`" for n in out[:limit])

    def note(self, labels: list[str], descriptions: dict[str, str] | None = None) -> str:
        """What the specifier should know about the nodes an issue advances."""
        rows = []
        for label in labels:
            if label in self.frontier:
                n = self.frontier[label]
                rows.append(f"- {label} (frontier — not yet formalized): "
                            f"{n.get('title', '')}. {n.get('statement', '')}".rstrip())
                for dep in n.get("uses", []) or []:
                    if dep in self.proved:
                        rows.append(f"    builds on {dep}: `{self.proved[dep].get('name', '')}`"
                                    f" — {self.proved[dep].get('title', '')}")
                if n.get("ground"):
                    rows.append(f"    ground: {n['ground']}")
                for entry in n.get("core", []) or []:
                    desc = (descriptions or {}).get(entry, "")
                    rows.append(f"    already proved toward it, do NOT restate: `{entry}`"
                                + (f" — {desc}" if desc else ""))
            elif label in self.proved:
                n = self.proved[label]
                rows.append(f"- {label} (proved: `{n.get('name', '')}`): {n.get('title', '')}")
        return "\n".join(rows)


def entry_descriptions(main_repo: str, benchmarks_dir: str, ids: set[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    if not ids:
        return out
    for f in _corpus_files(main_repo, benchmarks_dir) if benchmarks_dir else []:
        for e in _entries(os.path.join(main_repo, f)):
            if e.get("id") in ids:
                out[e["id"]] = " ".join(str(e.get("description") or "").split())[:400]
    return out


def add_core(frontier_text: str, label: str, entry_id: str) -> str | None:
    """Record `entry_id` in frontier node `label`'s `core` list — the library's own way of
    saying "this corpus entry proves part of that node, and the node stays on the
    frontier". A text edit, so the file's comments and layout survive. Returns the new
    text, the same text when the entry is already listed, or None when the node's block
    cannot be found (the caller then leaves the file alone)."""
    blocks = list(re.finditer(r"(?m)^\[\[node\]\][ \t]*\n", frontier_text))
    for i, b in enumerate(blocks):
        end = blocks[i + 1].start() if i + 1 < len(blocks) else len(frontier_text)
        block = frontier_text[b.end():end]
        if not re.search(rf'(?m)^label\s*=\s*"{re.escape(label)}"\s*$', block):
            continue
        core = re.search(r"(?m)^core\s*=\s*\[(.*?)\]\s*$", block, re.S)
        if core:
            have = re.findall(r'"([^"]+)"', core.group(1))
            if entry_id in have:
                return frontier_text
            items = ", ".join(f'"{x}"' for x in [*have, entry_id])
            new_block = block[:core.start()] + f"core = [{items}]" + block[core.end():]
        else:
            body = block.rstrip("\n")
            new_block = body + f'\ncore = ["{entry_id}"]\n' + block[len(body):].lstrip("\n")
            if not new_block.endswith("\n\n") and i + 1 < len(blocks):
                new_block = new_block.rstrip("\n") + "\n\n"
        return frontier_text[:b.end()] + new_block + frontier_text[end:]
    return None


# --- the module docstring -------------------------------------------------------


_NBSP = "\u00a0"
_CODE_SPAN = re.compile(r"`[^`]*`")


def _wrap(text: str, *, indent: str = "", width: int = LINE_LIMIT - 2) -> str:
    """Fill `text` to the line limit without breaking inside an inline `code span` (a
    span is made one unbreakable word for the fill, then given its spaces back)."""
    flat = _CODE_SPAN.sub(lambda m: m.group(0).replace(" ", _NBSP), " ".join(text.split()))
    filled = textwrap.fill(flat, width=width, initial_indent="", subsequent_indent=indent,
                           break_long_words=False, break_on_hyphens=False)
    return filled.replace(_NBSP, " ")


def module_doc(*, title: str, summary: str, definitions: list[tuple[str, str]],
               results: list[tuple[str, str]], references: list[str], tags: list[str],
               subjects: list[str]) -> str:
    """A module docstring BODY in the library's documentation standard (Mathlib's layout):
    `# Title`, a summary, `## Main definitions`, `## Main results`, `## References`,
    `## Tags`. `definitions`/`results` are `(name, one line)` pairs; `references` are
    ready bullets (`Bibliography.bullet`); `subjects` are the cell's JEL/MSC codes, written
    after the search terms. A section with nothing in it is left out — the standard asks
    for Main results only of a module that declares a theorem."""
    title = " ".join((title or "").split()).rstrip(".") or "Untitled"
    title = title[:1].upper() + title[1:]
    parts = [f"# {title}", _wrap(summary) if summary.strip() else ""]

    def bullets(rows: list[tuple[str, str]]) -> str:
        return "\n".join(_wrap(f"* `{name}`: {text}".rstrip(": ").rstrip(), indent="  ")
                         for name, text in rows)

    if definitions:
        parts += ["## Main definitions", bullets(definitions)]
    if results:
        parts += ["## Main results", bullets(results)]
    if references:
        parts += ["## References", "\n".join(_wrap(r, indent="  ") for r in references)]
    words = []
    for t in tags:
        t = " ".join(str(t).replace(",", " ").split())
        if t and not t.upper().startswith(("JEL", "MSC")) and t.lower() not in words:
            words.append(t.lower())
    if not words:
        words = [title.lower()]
    parts += ["## Tags", _wrap(", ".join([*words, *subjects]))]
    return "\n\n".join(p for p in parts if p)


def long_lines(text: str, limit: int = LINE_LIMIT) -> list[int]:
    """1-based numbers of the lines longer than the library's limit."""
    return [i for i, line in enumerate(text.split("\n"), 1) if len(line) > limit]
