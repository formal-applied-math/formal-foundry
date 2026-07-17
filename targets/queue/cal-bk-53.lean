/-
Copyright (c) 2026 Raphael Coelho. All rights reserved.
Released under Apache 2.0 license as described in the file LICENSE.
Authors: Raphael Coelho
-/
module

public import Mathlib
public import MathFin.BlackScholes.ChooserComposition
public import MathFin.BlackScholes.Lookback

-- pointers: MathFin/BlackScholes/ChooserComposition.lean, MathFin/BlackScholes/Lookback.lean
-- main-module: MathFin/BlackScholes/BarrierParity.lean
-- benchmark: benchmarks/mathematical_finance.json
-- benchmark-id: mf-barrier-parity
-- source-issue: 53
-- new-defs: knockInPayoff, knockOutPayoff

/-!
Knock-in plus knock-out equals vanilla (barrier parity).
-/

@[expose] public section

namespace MathFin

noncomputable def knockInPayoff {Ω : Type u} [MeasurableSpace Ω] (f : Ω → ℝ) (A : Set Ω) : Ω → ℝ :=
  Set.indicator A f

noncomputable def knockOutPayoff {Ω : Type u} [MeasurableSpace Ω] (f : Ω → ℝ) (A : Set Ω) : Ω → ℝ :=
  Set.indicator (Aᶜ) f

theorem integral_eq_knockIn_plus_knockOut {Ω : Type u} [MeasurableSpace Ω] (Q : MeasureTheory.Measure Ω) (A : Set Ω) (hA : MeasurableSet A) (f : Ω → ℝ) (hf : MeasureTheory.Integrable f Q) : ∫ ω, f ω ∂Q = (∫ ω, knockInPayoff f A ω ∂Q) + (∫ ω, knockOutPayoff f A ω ∂Q) := by sorry

end MathFin
