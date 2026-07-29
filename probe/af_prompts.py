"""af_prompts — extracted from autoformalize.py; see it for the pipeline overview."""
from __future__ import annotations

from house_context import build_drafter_prompt, build_system_prompt, extract_signatures

__all__ = ['JUDGE_SYSTEM', '_issue_prose', 'judge_messages', '_assistant', 'INTENT_SYSTEM', 'FORMALIZE_SYSTEM', 'INTENT_DEFS_ADDENDUM', '_DRAFTER_PROMPT', 'set_drafter_prompt', '_drafter_system', 'intent_messages', '_AGENTIC_PITFALLS', '_GATE_INSTRUCTIONS', 'render_gate_feedback', 'GOLF_SYSTEM']



# --- chat-mediated runners (claude: judge) ------------------------------------

JUDGE_SYSTEM = (
    "You are a faithfulness judge for autoformalized Lean statements — a SAFETY NET "
    "that catches GROSS failures, not a maximal-formality checker (a human makes the "
    "final call at merge). Given an issue's prose and a candidate Lean theorem, mark it "
    "faithful UNLESS it has a gross failure IN WHAT IT STATES: a fact it states is wrong, "
    "vacuous, or materially weaker than asked, a hypothesis or an inequality direction "
    "is wrong, or it silently drops part of a fact it claims to state. A candidate MAY "
    "faithfully formalize a SUBSET of a multi-part issue: when the drafter has DECLARED "
    "the omitted facts (they are listed below as 'declared deferred'), covering fewer "
    "facts than the issue lists is NOT a gross failure — judge the theorem as stated "
    "against the subset it claims, and let the deferred facts become follow-up issues. "
    "Only an UNDECLARED missing fact — a silent gap, absent from the deferred list — "
    "counts against faithfulness. ACCEPT reasonable abstractions: a real parameter standing for "
    "E[X], Var[X], a price, or a discount factor is fine (no measure-theoretic "
    "construction required), and a named quantity's definition MAY be inlined into its "
    "stated property (e.g. `(1+θ)*μ ≥ μ` faithfully renders 'the premium π = (1+θ)·μ "
    "satisfies π ≥ μ'). Do NOT fault a statement for omitting a hypothesis that is PROVABLE "
    "from the definitions it consumes: a zero-coupon-bond price built from `Real.exp` is "
    "automatically positive, so an explicit `0 < P` positivity hypothesis is unnecessary — its "
    "absence is not a gap. (A genuinely-needed side condition, like a denominator `≠ 0` that is "
    "NOT provable from the consumed defs, is still required.) "
    "Respond with ONLY a JSON object: "
    '{"faithful": true|false, "verdict": "<one line>", "issues": ["<gross gap>", ...]}.'
)



def _issue_prose(issue: dict) -> str:
    return f"{issue.get('title', '')}\n{issue.get('body', '')}"




def judge_messages(issue: dict, stub: str, deferred: list[str] | None = None) -> list[dict]:
    declared = ""
    if deferred:
        bullets = "\n".join(f"- {d}" for d in deferred)
        declared = ("\n\nDECLARED DEFERRED (the drafter intentionally left these for "
                    "follow-up issues; do NOT fail the subset for omitting them):\n"
                    f"{bullets}")
    return [{"role": "system", "content": JUDGE_SYSTEM},
            {"role": "user",
             "content": f"ISSUE:\n{_issue_prose(issue)}\n\nCANDIDATE:\n```lean\n{stub}\n```{declared}"}]




def _assistant(content: str) -> dict:
    """An assistant turn safe to re-send. Mistral 400s on an empty-content assistant message
    ("Assistant message must have either content or tool_calls, but not none") — which a
    free-tier empty reply produces once threaded into a repair round — so substitute a
    placeholder (caught #61 in the 2026-07-15 forced tick)."""
    return {"role": "assistant", "content": content or "(no output)"}




# --- two-stage draft: intent + agentic formalize (both claude) ----------------
#
# The split still stands, but both stages are Claude now: it SPECIFIES the intended
# statement in precise prose first (no Lean), then FORMALIZES it agentically
# (`claude -p` + the lean-lsp MCP, self-validating to elaboration). Leanstral no
# longer drafts either stage — it only PROVES. See
# docs/superpowers/specs/2026-07-14-leanstral-drafter-two-stage-design.md.

INTENT_SYSTEM = (
    "You are a mathematical-finance specifier for MathFin (a Lean 4 library on Mathlib). "
    "Given a GitHub issue (Task + Pointers), produce a PRECISE natural-language statement of the "
    "ONE theorem to formalize — every hypothesis and the full conclusion, unambiguous enough that "
    "a Lean expert could formalize it without seeing the issue. Do NOT write Lean. Name the exact "
    "MathFin/Mathlib objects it should be built from (e.g. `MathFin.zcb`), drawn from the "
    "declarations shown. Respond with ONLY a JSON object: "
    '{"statement": "<precise prose>", "objects": ["MathFin.zcb", ...], "module_name": "<CamelCase>", '
    '"benchmark_id": "mf-<area>-<slug>", "docstring": "<one line>", "deferred": ["<fact left out>", ...]}. '
    "`deferred` is [] when the statement covers the whole issue; when you specify a faithful SUBSET, "
    "list the omitted facts (each a short phrase) so they become follow-up issues. Never weaken or "
    "silently drop a fact you state. "
    "The `docstring` is ONE terse line stating WHAT is defined or proved — house register: no "
    "marketing words (powerful/seamless/cutting-edge), no signpost openers (moreover/furthermore/"
    "it is worth noting), no vacuous \"plays a role\" filler. State it; do not sell it."
)



FORMALIZE_SYSTEM = (
    "You are an autoformalization model for MathFin (Lean 4 on Mathlib). Given a precise INTENDED "
    "STATEMENT in prose plus the available declarations, produce ONE Lean 4 theorem that formalizes "
    "it EXACTLY, ending in `:= by sorry` (state only — no proof). Requirements:\n"
    "- Output exactly one ```lean block: a single `theorem NAME <binders> : <conclusion> := by sorry`.\n"
    "- CONSUME the named MathFin/Mathlib declarations rather than reproving or inlining them (e.g. use "
    "`MathFin.zcb r t T`, do not re-derive the bond price).\n"
    "- ASCII-parseable operators: `^` for powers (write `σ^2`, never the Unicode superscript `σ²`); "
    "`*` for products; `Real.exp`/`Real.log`/`Real.sqrt`.\n"
    "- Render every hypothesis and the full conclusion of the intended statement — do not weaken, "
    "drop a hypothesis, or flip an inequality.\n"
    "- Lint-clean for the library's CI: every `def`/`abbrev`/`structure` carries a `/-- … -/` "
    "docstring immediately above it, and definition names are lowerCamelCase (theorem names "
    "stay snake_case).\n"
    "- State at the NATURAL level of generality: when the fact is algebra over given quantities, "
    "do not hard-wire a concrete model or curve the claim does not require. Prefer the structural "
    "hypothesis that says exactly what is needed — `s.Nonempty` rather than a member-witness "
    "`x ∈ s`, `A ≠ 0` rather than assuming positivity of a quantity that is already provably "
    "positive from its definition.\n"
    "- Definitions bind their arguments in the signature (`def f (x : ℝ) : ℝ := …`), never a "
    "lambda against a `∀` ascription; write anonymous functions with `↦`.\n"
    "- Exactly ONE `sorry` — the CORE theorem, first. You MAY add corollaries AFTER it "
    "(the issue-shaped instantiation of a general core, or per-fact projections of an "
    "`∧`-bundle) as additional theorems proved by sorry-free TERMS applying the core."
)







# the defs-route addendum (F1): the issue's primitives don't exist yet, so the
# intent must also SPECIFY the 1-3 definitions the module introduces.
INTENT_DEFS_ADDENDUM = (
    "\n\nTHIS ISSUE NEEDS NEW DEFINITIONS: the library does not yet provide the primitives "
    "the statement should be expressed through. In the same JSON, also emit "
    '"definitions": [{"name": "<camelCase>", "signature": "<Lean type>", '
    '"meaning": "<one line>", "built_from": ["<existing Mathlib/MathFin constants>"]}, ...] '
    "— 1 to 3 definitions, each buildable from EXISTING constants (never a free-floating "
    "wrapper), and specify the statement so the theorem is EXPRESSED THROUGH these new "
    "definitions. When the natural shape is a GENERAL core plus the issue-shaped "
    'instantiation, also emit "corollary": {"name": "<snake_case>", "statement": '
    '"<one line>"} — the core carries the proof, the corollary applies it.'
    ' For EACH definition also emit "examples": ["<def> <explicit small inputs> = '
    '<intended value>", ...] — 1-2 concrete instance checks whose value you compute by '
    "hand from the issue's semantics; the formalizer turns each into an "
    "`example … := by norm_num`, so a wrong sign or normalization is caught before proving."
)




# --- Drafter authority wiring (H1) -------------------------------------------
# The drafter (intent + formalize) wrote statements with no pins and no house
# statement-design rules — hence hallucinated constants and depth-gate deaths.
# `set_drafter_prompt` wires the pins + statement-design preamble in once at
# pipeline start (patterns.md read live); '' until wired, so the base system
# prompts are unchanged for callers that never wire it.
_DRAFTER_PROMPT: str = ""




def set_drafter_prompt(main_repo: str) -> None:
    """Wire the live drafter authority (pins + the statement-design section of
    patterns.md) into the intent/formalize system prompts. Call once at pipeline
    start; reads patterns.md live, not at import time."""
    global _DRAFTER_PROMPT
    _DRAFTER_PROMPT = build_drafter_prompt(main_repo)




def _drafter_system(base: str) -> str:
    """Prepend the wired drafter authority to a base drafter system prompt."""
    return (_DRAFTER_PROMPT + "\n" + base) if _DRAFTER_PROMPT else base




def intent_messages(issue: dict, context_pack: str, feedback: str | None = None,
                    route: str = "theorem",
                    prior_unknowns: list[str] | None = None,
                    prior_lessons: str | None = None) -> list[dict]:
    user = f"ISSUE #{issue.get('number')}: {_issue_prose(issue)}\n"
    if context_pack:
        user += "\nAvailable declarations you may reference:\n" + context_pack
    if route == "defs":
        user += INTENT_DEFS_ADDENDUM
        if prior_unknowns:
            user += ("\nPrior attempts guessed these MISSING declarations — define "
                     "equivalents where sensible: " + ", ".join(prior_unknowns))
    if prior_lessons:   # item K: what earlier TICKS tried + a rotating diversity nudge
        user += "\n\n" + prior_lessons
    if feedback:
        user += ("\n\n" + feedback
                 + "\nProduce a REVISED intent that fixes this; respond with the same JSON shape.")
    return [{"role": "system", "content": _drafter_system(INTENT_SYSTEM)},
            {"role": "user", "content": user}]




# Hard-won Lean pitfalls (repurposed from the retired completion-repair heuristics): the
# recurring elaboration traps under our pin, given to the agentic drafter UPFRONT so it does
# not rediscover them round by round. It still fixes everything else live via lean_diagnostics.
_AGENTIC_PITFALLS = (
    "COMMON PITFALLS under our pin (avoid these upfront; lean_diagnostics catches the rest):\n"
    "- Apply every MathFin/Mathlib definition to ALL its arguments (`MathFin.zcb r t T`, never "
    "`MathFin.zcb r`) — a partial application is a `… → …` where a value is expected.\n"
    "- `autoImplicit false` is on: do NOT write an explicit universe (`Type u`) or a `universe` "
    "decl; use `Type*` / `Sort*`.\n"
    "- For `ℝ≥0` add `open scoped NNReal` and write `ℝ≥0` (bare `ℝ ≥ 0` misparses as a Prop).\n"
    "- A stuck typeclass metavariable (`?m…`): name the ambiguous implicit (`(μ := μ)`) or "
    "`@`-apply, so instance search is not left guessing.\n"
    "- For an unknown identifier, use lean_loogle / lean_leansearch to find the real name + "
    "namespace before guessing; do not invent a constant."
)









# --- semantic-gate feedback (the repair cascade's re-draft signal) ------------
#
# The only repaired failure class used to be compilation (formalize_with_repair);
# every semantic gate was a terminal skip, so the drafter was never told WHY a
# clean-elaborating draft was rejected (design: 2026-07-17-semantic-repair-cascade).
# Each gate gets a repair DIRECTION here; the block is sent to BOTH stages of the
# next attempt (Claude may need to re-frame the intent, or the agentic formalize
# step must stop inlining what it should consume).

_GATE_INSTRUCTIONS = {
    "intent": "Respond with ONLY one JSON object carrying statement, objects, "
              "module_name, benchmark_id, docstring, deferred — no prose around it.",
    "formalize": "The intended statement could not be rendered into elaborating Lean "
                 "after all repair rounds. Re-specify it using ONLY objects from the "
                 "declarations shown (name each exactly); prefer fewer, concrete "
                 "objects over prose-only quantities.",
    "depth": "The statement's TYPE consumes no definition from the issue's pointer "
             "modules — it restates the mathematics over raw reals (e.g. via `let` or "
             "inlined formulas). Re-state the theorem so its TYPE is EXPRESSED THROUGH "
             "the named MathFin declarations, fully applied (e.g. `MathFin.zcb r 0 T`); "
             "never re-derive or inline their formulas.",
    "trivial": "The statement is closed by `rfl`/`simp` alone — a definitional "
               "restatement with no mathematical content. State the SUBSTANTIVE fact "
               "the issue asks for: an identity or inequality between INDEPENDENTLY "
               "defined quantities, not a definition unfolded into itself.",
    "instance_probe": "A new definition ships no concrete-instance check. For EACH new "
                      "def add 1-2 `example : <def> <explicit small inputs, e.g. ![1, 2] "
                      "over Finset (Fin 2)> = <intended value> := by norm_num [<def>]` (or "
                      "`decide`), the value taken from the issue's semantics — a wrong "
                      "sign/normalization then fails to elaborate.",
    "vacuous": "The hypotheses are mutually contradictory (`False` is provable from "
               "them), so the theorem is vacuously true. Fix the hypothesis set — "
               "check inequality directions and degenerate parameter values.",
    "false": "The NEGATION of the conclusion was PROVED under the hypotheses — the "
             "statement is false AS WRITTEN. First suspect the RENDERING: a flipped "
             "inequality or sign, swapped arguments, or a missing hypothesis — fix "
             "that, and do NOT weaken a claim that is genuinely true. But if a "
             "specific conjunct is FALSE as the ISSUE itself states it (a real "
             "counterexample exists — e.g. an invariance written for all `c` that "
             "holds only for `c > 0`), stop fighting it: DROP that conjunct from the "
             "conclusion, add a one-line `CORRECTION: …` to `deferred` naming what the "
             "issue got wrong and the fix, and prove the TRUE remainder through the "
             "same definitions.",
    "unfaithful": "A faithfulness judge found the statement diverges grossly from "
                  "the issue. Address each listed divergence without weakening any "
                  "fact you state.",
    "newdef_depth": "The theorem's TYPE must be stated THROUGH the drafted definitions — "
                    "apply each drafted def in the hypotheses/conclusion, never restate "
                    "their formulas inline; and the module must actually contain the "
                    "1-3 definitions.",
    "ungrounded": "A drafted definition is a free-floating wrapper: its body must be "
                  "BUILT FROM existing Mathlib/MathFin constants (an integral, Real.exp, "
                  "max, an existing MathFin price), not a bare variable or a "
                  "self-referential shell.",
}




def render_gate_feedback(gate: str, detail: str, stub: str | None) -> str:
    """The re-draft feedback block for a semantic-gate rejection: the rejected stub
    (when one exists), the gate's own verdict, and the gate-specific revision
    instruction."""
    txt = f"PREVIOUS ATTEMPT — rejected by the `{gate}` gate"
    if detail:
        txt += f": {detail}"
    txt += "\n"
    if stub:
        txt += f"```lean\n{stub.strip()}\n```\n"
    txt += "REVISE: " + _GATE_INSTRUCTIONS.get(
        gate, "Fix the reported failure without weakening any fact.")
    return txt




GOLF_SYSTEM = (
    "You are polishing an ACCEPTED, kernel-checked Lean 4 proof to the library's house "
    "register. Rewrite ONLY proof bodies (what follows each `:=`): every statement, "
    "name, docstring, import and definition stays byte-identical. House idioms: the "
    "certificate over search (`mul_nonneg h₁ h₂` over `nlinarith`, `hA.ne'` over "
    "`linarith`); bare proof TERM over `by exact`; no `set … with h` whose equation is "
    "never used; fold `have h := e; simp at h; exact h` into `simpa using e`; fewer "
    "`have`s — surface the shape with `suffices`/`show`; `↦` over `=>`; never introduce "
    "`sorry` or `?`-suggestion tactics. If the proof is already minimal, return it "
    "unchanged. Output exactly one ```lean block containing the FULL file."
)
