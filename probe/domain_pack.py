"""domain_pack — the single access path to a domain's content.

The foundry's prover loop, kernel gates and faithfulness discipline are
field-neutral; what differs per target library is a **domain pack**: the house
doctrine, the prompt prose, the worked-example constants, the issue-label
vocabulary, and the templates for the Lean it EMITS. This module loads one.

    pack = domain_pack.load("mathfin")
    prompt = pack.prompt("judge-system")

Pass the pack DOWN as a parameter. There is deliberately no module-level default
instance: two domains must be able to live in one process (a matrix CI run over
mathfin and econometrics is the whole point), and a global would make the second
one silently inherit the first's namespace.

── Why the namespace is data, not prose ──
Five sites GENERATE Lean keyed on the namespace — the depth and defs gate meta
blocks (`env.find? \\`MathFin.foo`), the emitted module skeleton, the splice
anchor the agentic drafter's output is read back through, and the pointer /
location / import regexes. No amount of prompt extraction reaches those; they are
templates over `namespace` and `lake_root`, which is why both are pack fields and
the regexes below are DERIVED rather than stored.

── The trailing-newline contract ──
A prompt is a string; a file is a string plus a terminator. `_read_text` strips
exactly ONE trailing newline, so a prompt with no trailing newline round-trips
through a file that ends with one (which git and every editor want anyway), and a
prompt that must end with "\\n" is stored with a blank line at EOF. Byte-identity
is the acceptance criterion for the extraction — `test_domain_pack_golden.py`
proves it.

Stdlib only, like the rest of `probe/`.

Design of record:
formal-mathfin/docs/plans/2026-08-09-program-execution/02-foundry-domain-packs.md
"""
from __future__ import annotations

import json
import os
import re
import tomllib
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

__all__ = ["DomainPack", "Dep", "load", "default_pack_root", "available"]

_PLACEHOLDER_RE = re.compile(r"\{\{([a-z_]+)\}\}")


def default_pack_root() -> str:
    """`domains/` beside `probe/` — the foundry root's pack directory."""
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "domains")


@dataclass(frozen=True)
class Dep:
    """One pinned dependency, as the prompt's pin block names it."""

    manifest: str   # package name in the target's lake-manifest.json
    label: str      # how the pin block prints it
    upstream: str   # owner/repo of record


@dataclass(frozen=True)
class DomainPack:
    """One target library's content. Frozen: a pack is read once and passed down."""

    name: str

    # --- identity the emitted Lean is keyed on ---
    namespace: str
    lake_root: str
    own_namespaces: tuple[str, ...]

    # --- prose identity for the prompt templates ---
    field: str
    dependencies_inline: str
    deps: tuple[Dep, ...]

    # --- the target repo ---
    slug: str
    benchmark: str
    domain: str

    # --- infra the harness needs ---
    lean_lsp_container: str
    verify_image: str
    scratch_module: str

    # --- emitted-module shape ---
    opens: tuple[str, ...]
    splice_anchor: str
    license: str

    # --- vocabulary + prose ---
    areas: Mapping[str, str]
    exemplars: Mapping[str, str]
    prompts: Mapping[str, str]
    gate_instructions: Mapping[str, str]
    house_doctrine: str
    statement_design_fallback: str

    # --- derived: the namespace-keyed regexes -------------------------------
    #
    # Built from `lake_root`/`namespace` rather than stored, so a pack cannot
    # declare a namespace and then disagree with itself about how to parse it.

    @property
    def pointer_re(self) -> re.Pattern[str]:
        """Repo-relative `<LakeRoot>/…/X.lean` paths named in an issue body."""
        return re.compile(rf"{re.escape(self.lake_root)}/[\w/]+\.lean")

    @property
    def location_re(self) -> re.Pattern[str]:
        """An issue's explicit `location: <LakeRoot>/…/X.lean` placement line."""
        return re.compile(rf"(?im)^\s*location:\s*({re.escape(self.lake_root)}/[\w/]+\.lean)")

    @property
    def import_re(self) -> re.Pattern[str]:
        """`public import <Namespace>.X` lines, for the unused-import trim."""
        return re.compile(rf"^public import ({re.escape(self.namespace)}\.\S+)[ \t]*\n",
                          re.MULTILINE)

    # --- derived: naming ------------------------------------------------------

    def qualified(self, decl: str) -> str:
        """`foo` -> `MathFin.foo`, the name the gate meta blocks look up."""
        return f"{self.namespace}.{decl}"

    def module_of(self, pointer: str) -> str:
        """`MathFin/FixedIncome/ZCB.lean` -> the Lean module `MathFin.FixedIncome.ZCB`."""
        stem = pointer[:-5] if pointer.endswith(".lean") else pointer
        return stem.replace("/", ".")

    def section_for_area(self, area: str) -> str:
        """An issue's `area:` label -> its `<LakeRoot>/<Section>/` subdirectory.
        Unmapped areas fall back to a CamelCase of the label, which a new
        subdirectory + the umbrella import absorb."""
        if area in self.areas:
            return self.areas[area]
        return "".join(p.capitalize() for p in re.split(r"[-_ ]+", area or "") if p)

    def main_module(self, section: str, module_name: str) -> str:
        return f"{self.lake_root}/{section}/{module_name}.lean"

    def import_line(self, pointer: str) -> str:
        return f"public import {self.module_of(pointer)}"

    # --- derived: prose -------------------------------------------------------

    def prompt(self, key: str) -> str:
        """A rendered prompt from `prompts/<key>.md`. KeyError names the pack, so a
        second domain missing a prompt fails loudly at the call site rather than
        sending the model an empty system message."""
        try:
            return self.prompts[key]
        except KeyError:
            raise KeyError(
                f"domain pack {self.name!r} has no prompt {key!r} "
                f"(expected domains/{self.name}/prompts/{key}.md)") from None

    def gate_instruction(self, gate: str, default: str) -> str:
        return self.gate_instructions.get(gate, default)

    def pin_block(self, toolchain: str, revs: Mapping[str, str]) -> str:
        """The `── PINS ──` block every drafter/prover prompt opens with, one row
        per declared dependency. `revs` maps a dep's `manifest` name to its rev."""
        rows = "".join(f"- {d.label}: {d.upstream} @ {revs.get(d.manifest, '?')}\n"
                       for d in self.deps)
        return f"- Lean toolchain: {toolchain}\n" + rows


# --- loading ------------------------------------------------------------------


def _read_text(path: str) -> str:
    """A pack text file, minus exactly one trailing newline (the terminator)."""
    with open(path, encoding="utf-8") as f:
        body = f.read()
    return body[:-1] if body.endswith("\n") else body


def _substitutions(cfg: dict, exemplars: Mapping[str, str]) -> dict[str, str]:
    lib = cfg["library"]
    subs = {
        "namespace": lib["namespace"],
        "lake_root": lib["lake_root"],
        "field": lib["field"],
        "dependencies_inline": lib["dependencies_inline"],
    }
    subs.update({k: v for k, v in exemplars.items() if not k.startswith("_")})
    return subs


def _render(text: str, subs: Mapping[str, str], *, where: str) -> str:
    """Substitute `{{key}}` placeholders. Unknown placeholders RAISE — a silently
    unrendered `{{namespace}}` would reach a model verbatim, and the whole point of
    the pack is that the prompts are correct for the target they name."""
    def one(m: re.Match[str]) -> str:
        key = m.group(1)
        if key not in subs:
            raise KeyError(f"{where}: unknown placeholder {{{{{key}}}}} "
                           f"(pack defines {sorted(subs)})")
        return subs[key]

    return _PLACEHOLDER_RE.sub(one, text)


def available(name: str, root: str | None = None) -> bool:
    return os.path.isfile(os.path.join(root or default_pack_root(), name, "target.toml"))


DEFAULT_NAME = "mathfin"


def name_from_config(config_path: str) -> str:
    """`[domain] name` from pipeline.toml — the one key the pack contract adds
    there. Falls back to the flagship, so an old config keeps working unchanged."""
    try:
        with open(config_path, "rb") as f:
            return tomllib.load(f).get("domain", {}).get("name") or DEFAULT_NAME
    except (OSError, tomllib.TOMLDecodeError):
        return DEFAULT_NAME


def load(name: str = "mathfin", root: str | None = None) -> DomainPack:
    """Read `domains/<name>/` into a frozen `DomainPack`."""
    base = os.path.join(root or default_pack_root(), name)
    cfg_path = os.path.join(base, "target.toml")
    if not os.path.isfile(cfg_path):
        raise FileNotFoundError(f"no domain pack {name!r} at {base}")
    with open(cfg_path, "rb") as f:
        cfg = tomllib.load(f)

    lib, repo, infra, module = cfg["library"], cfg["repo"], cfg["infra"], cfg["module"]

    with open(os.path.join(base, "exemplars.json"), encoding="utf-8") as f:
        exemplars = {k: v for k, v in json.load(f).items() if not k.startswith("_")}
    with open(os.path.join(base, "gate-instructions.json"), encoding="utf-8") as f:
        raw_gates = json.load(f)

    subs = _substitutions(cfg, exemplars)

    prompt_dir = os.path.join(base, "prompts")
    prompts = {}
    if os.path.isdir(prompt_dir):
        for fn in sorted(os.listdir(prompt_dir)):
            if fn.endswith(".md"):
                key = fn[: -len(".md")]
                prompts[key] = _render(_read_text(os.path.join(prompt_dir, fn)), subs,
                                       where=f"{name}/prompts/{fn}")

    gate_instructions = {k: _render(v, subs, where=f"{name}/gate-instructions.json[{k}]")
                         for k, v in raw_gates.items()}

    return DomainPack(
        name=name,
        namespace=lib["namespace"],
        lake_root=lib["lake_root"],
        own_namespaces=tuple(lib.get("own_namespaces") or [lib["namespace"]]),
        field=lib["field"],
        dependencies_inline=lib["dependencies_inline"],
        deps=tuple(Dep(manifest=d["manifest"], label=d["label"], upstream=d["upstream"])
                   for d in lib.get("deps", [])),
        slug=repo["slug"],
        benchmark=repo["benchmark"],
        domain=repo["domain"],
        lean_lsp_container=infra["lean_lsp_container"],
        verify_image=infra["verify_image"],
        scratch_module=infra["scratch_module"],
        opens=tuple(module["opens"]),
        splice_anchor=module["splice_anchor"],
        license=module["license"],
        areas=MappingProxyType(dict(cfg.get("areas", {}))),
        exemplars=MappingProxyType(dict(exemplars)),
        prompts=MappingProxyType(prompts),
        gate_instructions=MappingProxyType(gate_instructions),
        house_doctrine=_render(_read_text(os.path.join(base, "house.md")), subs,
                               where=f"{name}/house.md"),
        statement_design_fallback=_render(
            _read_text(os.path.join(base, "statement-design.md")), subs,
            where=f"{name}/statement-design.md"),
    )
