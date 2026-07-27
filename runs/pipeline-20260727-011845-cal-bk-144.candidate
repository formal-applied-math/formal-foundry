/-
Copyright (c) 2026 Raphael Coelho. All rights reserved.
Released under Apache 2.0 license as described in the file LICENSE.
Authors: Raphael Coelho
-/
module

public import Mathlib
public import MathFin.Foundations.WienerIntegralL2

-- pointers: MathFin/Foundations/WienerIntegralL2.lean
-- main-module: MathFin/FixedIncome/WienerFubiniDeterministic.lean
-- benchmark: benchmarks/mathematical_finance.json
-- benchmark-id: mf-foundations-wiener-fubini-deterministic
-- source-issue: 144
-- deferred: Refactoring `FixedIncome/VasicekBondPrice.lean`'s integrated-rate step to consume this Fubini instance in place of its hand-rolled computation; Instantiating the identity to the specific Vasicek/Ornstein-Uhlenbeck kernel σ(s,u) = e^{-a(s-u)} and re-deriving `vasicekBondPrice_*` from it; Coverage row, AxiomAudit entry, ledger update, and `docs/bridges.md` update recording this instance

/-!
Deterministic-kernel stochastic Fubini: the Bochner integral of `wienerIntegralLp` over a causal kernel family equals `wienerIntegralLp` of the order-swapped kernel.
-/

set_option autoImplicit false

@[expose] public section

namespace MathFin

open MeasureTheory ProbabilityTheory
open scoped NNReal ENNReal

/-- Deterministic-kernel stochastic Fubini for the Wiener integral: given a causal,
jointly integrable family of deterministic kernels `σ s ∈ L²((0, T])` indexed by
`s ∈ (0, T]` (each `σ s` supported, up to a.e. equality, on `(0, s]`), the Bochner
integral over `s ∈ (0, T]` of the Wiener integrals `wienerIntegralLp B hB T (σ s)`
equals the Wiener integral of the order-swapped kernel `g`, where
`g u = ∫ s in (u, T], σ s u ∂volume` (a.e. in `u`). This is the deterministic-integrand
instance of the stochastic Fubini theorem
`∫₀ᵀ (∫₀ˢ σ(s,u) dW_u) ds = ∫₀ᵀ (∫ᵤᵀ σ(s,u) ds) dW_u`, phrased entirely through
`WienerIntegralL2.wienerIntegralLp`. -/
theorem wienerFubini_deterministic
    {Ω : Type*} {mΩ : MeasurableSpace Ω} {μ : Measure Ω} [IsProbabilityMeasure μ]
    {B : ℝ≥0 → Ω → ℝ} (hB : IsPreBrownianReal B μ) (T : ℝ≥0)
    (σ : ℝ → Lp ℝ 2 (volume.restrict (Set.Ioc (0 : ℝ) (T : ℝ))))
    (hσ_causal : ∀ s ∈ Set.Ioc (0 : ℝ) (T : ℝ),
      (σ s : ℝ → ℝ) =ᵐ[volume.restrict (Set.Ioc s (T : ℝ))] 0)
    (hσ_int : Integrable σ (volume.restrict (Set.Ioc (0 : ℝ) (T : ℝ))))
    (g : Lp ℝ 2 (volume.restrict (Set.Ioc (0 : ℝ) (T : ℝ))))
    (hg : (g : ℝ → ℝ) =ᵐ[volume.restrict (Set.Ioc (0 : ℝ) (T : ℝ))]
      fun u => ∫ s in Set.Ioc u (T : ℝ), (σ s : ℝ → ℝ) u ∂volume) :
    ∫ s in Set.Ioc (0 : ℝ) (T : ℝ),
        WienerIntegralL2.wienerIntegralLp B hB T (σ s) ∂volume =
      WienerIntegralL2.wienerIntegralLp B hB T g := by sorry

end MathFin
