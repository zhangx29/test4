# Op-FVA (FBA deduction) reconciliation

## Summary

XVA system reports 18.9mm of lifetime Op-FVA RWA for the Nat Gas Prepay (request 385212); the manual prover reports 75.2mm, 3.98 times as much. The gap is a defect in the Constant curve roll-down branch of `optionFVA_V2` in Kva.fs. It looked up funding cost with `fVAEL[k − j]`, a bucket-index difference, where it should have used the time elapsed between bucket dates.

| Item | Finding |
| --- | --- |
| Break | Lifetime FBA-deduction RWA: prover 75,165,971 vs LiTES Op-FVA 18,894,684 (×3.98) |
| Not the cause | CER (11.5%), tax (26.4–26.5%), discounting and the lifetime trapezoid integral: both numbers rebuild exactly through the same integral |
| Cause | Old Constant branch: future bucket k, seen from bucket j, was weighted by `fVAEL[k − j]`. On an uneven grid (weekly, then monthly, then quarterly) every later bucket j picked up the short early accruals, so the deduction lost about 7–9% per grid step |
| Today's change | `constantWeights` uses elapsed time snapped to the nearest bucket. Right in principle; needs interpolation instead of snapping, a timing-convention check, and two smaller fixes |
| Expected after fix | Constant style ≈ 43–49mm (18.9mm ÷ 0.39–0.44). The prover's 75mm is likely Forward style or a different curve; confirm before comparing |
| Interim | Price and capitalise on the prover's figure, not 18.9mm, until the fix passes the tests in this doc |

The defect understates the FBA deduction most on long-dated, uncollateralised trades where the bank owes the client, which is where OSFI CAR ¶68 has the most capital effect.

## How option FVA is calculated

`optionFVA_V2` turns simulated exposure into an after-tax capital deduction for every time bucket. It runs once for the portfolio with the trade (Test) and once without it (Base).

1. **Inputs.**
   - `pVByPaths[i,j]`: portfolio value on path i at bucket j, discounted to today.
   - `dFByPaths[i,j]`: path discount factors.
   - `fVAEL[k]`: funding cost of bucket k (spread × the bucket's length).
   - `ratioRBTNeg` / `ratioRBTPos`: weights on the part of exposure the bank owes (FBA) and is owed (FCA); default 1.
2. **FVA on each path at each bucket.** From bucket j, add up the funding cost of all exposure still to come:
   - FVA\[i,j\] = Σ over k ≥ j of ( min(V\_ik, 0) × ratioNeg\_k + max(V\_ik, 0) × ratioPos\_k ) × weight\[j,k\].
   - **Forward style:** weight = `fVAEL[k]`, today's forward spread for bucket k's own date.
   - **Constant (roll-down) style:** weight = the cost on today's spot curve over the elapsed time t\_k−1 − t\_j to t\_k − t\_j. This is the branch with the defect.
   - A liability (V < 0) gives a negative FVA, i.e. a funding benefit.
3. **Convert to each bucket's own dollars.** `makeFVUSDMatrix` divides FVA and portfolio value by the path discount factor. Paths whose discount factor falls to `minDF` or below are flagged in `zeroDiscCounts`.
4. **Statistics per bucket**, using only unflagged paths:
   - E\[FVA\_j\] = the mean of FVA across paths. This is the option's forward.
   - The regression (below) gives the option's volatility.
5. **Price the option.** A normal (Bachelier) put on FVA with forward E\[FVA\_j\], strike 0, a one-year horizon and the explained vol. It pays when FVA is a net benefit. Capital\_j = put value × (1 − taxRate).
6. **Downstream.** Capital = Test − Base; RWA\_t = capital\_t ÷ CER (11.5%); lifetime RWA = the trapezoid sum of RWA\_t × D\_t × dt.

**The regression sets only the volatility.** At each bucket, `OpsUtils.simpleRegression` fits FVA ≈ alpha + beta × portfolio value across paths.

- `totalVol` = standard deviation of FVA across paths; `residualsVol` = standard deviation of what the fit leaves unexplained.
- `currentVol` = √(totalVol² − residualsVol²), the explained vol, which becomes the option vol.
- The forward is the plain mean, not the fitted value.
- When the option is deep in the money, as on the prepay, the vol barely changes the value, so the regression could not cause the 4x gap.
- At t0 every path has the same value, so alpha and beta are undefined. The explained vol is then 0, so this is harmless, but a zero-variance guard would be safer.

## Time buckets vs index

Every array in the calculation is indexed by bucket number, but the buckets are not evenly spaced. "One bucket later" can mean one week or three months, so any step that needs a length of time must take it from `timeInYears` or from a value that already has Δt built in.

| Index | Dates | Spacing (Δt) |
| --- | --- | --- |
| 0 | 2026-11-13 | t = 0 |
| 1–3 | weekly / bi-weekly | about 0.02–0.04y |
| 4–20 | monthly, to about 1.5y | about 0.08y |
| 21–43 | quarterly, to 7.25y | 0.25y |

### Where index or time is used

| Step | What it uses | Safe? |
| --- | --- | --- |
| Exposure `pVByPaths[i,k]` | Value at bucket k's date; the index is only a label | Yes |
| Funding cost `fVAEL[k]` | Spread × Δt of bucket k, so it carries that bucket's length | Yes, if used with the right k |
| Forward weights | `fVAEL[k]`: bucket k's own length at its own date | Yes |
| Old Constant weights | `fVAEL[k − j]`: treats "k − j buckets after j" as the same length of time as bucket k − j from today | **No: the defect** |
| New Constant weights (today) | Cost over the elapsed time t\_k−1 − t\_j to t\_k − t\_j, snapped to the nearest bucket | Yes in principle; interpolate instead of snapping |
| Conversion to bucket dollars | Each value divided by its own bucket's discount factor | Yes |
| Regression and option | Only data from that bucket's date; option horizon is a fixed 1 year | Yes |
| Lifetime integral | Trapezoid using the actual dt between dates | Yes |

### Worked example: column j = 14 (t ≈ 1.0y)

| Future bucket k | Its period | Correct cost covers | Old code used | Length that represents |
| --- | --- | --- | --- | --- |
| 15 | 1.00 → 1.08y | 0.08y | `fVAEL[1]` | 0.02y (one week) |
| 16 | 1.08 → 1.17y | 0.08y | `fVAEL[2]` | 0.02y |
| 21 | 1.50 → 1.75y | 0.25y | `fVAEL[7]` | 0.08y |
| 35 | 5.0 → 5.25y | 0.25y | `fVAEL[21]` | 0.25y (right length, by coincidence) |

Seen from 1y out, the large exposures between 1y and 5y were charged for about 1.5 years of funding instead of about 4, and at short-tenor spreads. Each step further along the grid adds more mismatched buckets, which produces the roughly constant per-step decay in the trace. Column 0 is unaffected because k − 0 = k, which is why t0 roughly ties.

### Rule for reviewing code

Anything that represents time (accrual, spread × Δt, discounting, an option horizon) must come from `timeInYears` or from a value already measured in time. An index difference like `k − j`, or a loop over "the next n buckets", is only safe on an evenly spaced grid.

Two quick checks catch it in any function:

- **Grid density:** add extra weekly buckets without changing anything else; lifetime results should barely move.
- **Flat curve:** Forward and Constant should give the same result.

Run both on the default and CVA lifetime functions in Kva.fs as well.

## Evidence

Both figures rebuild exactly through the same lifetime integral, so the whole gap sits in the capital value per bucket. The engine starts 15% above the prover at t0, then loses 7–9% at every grid step whether the step is a week or a quarter.

&#91;embedded content: Prover OptionFVA column × 0.735 and RWA\_385212\_Lifetime\_Trace column H, read from screenshots · 20 of 44 buckets\]

### Reconstruction

| Source | Rebuilt as | Result |
| --- | --- | --- |
| LiTES engine | Trace column H ÷ 11.5% × D\_t, trapezoid | 18.89mm (reported 18,894,684; diff 69) |
| Prover | OptionFVA × (1 − 26.5%) ÷ 11.5% × D\_t, same trapezoid | 75.03mm (exact at an implied 26.4% tax) |

### Signatures of the defect

- **Below intrinsic value.** At 1y the engine's pre-tax value is 1.01mm against an E\[FVA\] of 3.68mm. A strike-0 put can never be worth less than |E\[FVA\]|, so the engine's E\[FVA\] vector itself was wrong.
- **Decay per step, not per year.** Step ratios run 0.914 → 0.856 through the weekly and monthly buckets, and the decay slows (0.868) when the step jumps to 0.25y.
- **Economics.** Expected MTM is still −151mm at 1y and −125mm at 2y, yet the engine kept only 22% of its t0 FBA at 1y.

### Simulation of the old indexing

Re-running `fVAEL[k − j]` against time-based weights on the 385212 grid and MTM profile reproduces the pattern:

| Measure | Flat 70bp curve | Upward curve (30 → 110bp) |
| --- | --- | --- |
| Loss per grid step | 0.94 → 0.93 | 0.92 → 0.91 |
| Old ÷ correct at 3m / 1y / 2y | 0.77 / 0.46 / 0.27 | 0.71 / 0.36 / 0.20 |
| Lifetime, old ÷ correct | 0.44 | 0.39 |

The simulated 1y ratio (0.36–0.46) is above the observed 0.27. The rest of the gap is consistent with the prover using the Forward style, which values later funding at higher forward spreads.

## Code review and fix

Today's `constantWeights` change (branch bugfix/lcr\_funding\_ia\_fix\_for\_provbc) fixes the root cause in principle by weighting on elapsed time. Five points remain before it is ready.

| # | Point | Action |
| --- | --- | --- |
| 1 | Nearest-bucket snapping. Elapsed times rarely land on a grid date, so neighbouring k can map to the same bucket (zero weight) or skip one. Totals still telescope, but cost lands on the wrong exposures | Interpolate the cumulative cost curve linearly in time (snippet below) |
| 2 | Timing convention. Forward includes k = j (`fVAEL[j]`, summed from j); Constant sets `k <= j → 0`. One of the two is a bucket out | Settle with the flat-curve test; align both branches |
| 3 | `fVAExpected = Array.zeroCreate<float> (numPaths)` is indexed by bucket | Change to `numTB`; it would throw with fewer paths than buckets |
| 4 | The put is on net FVA, including `max(pV,0) × ratioRBTPos`, so FCA offsets FBA. OSFI CAR ¶68 requires FBA gross of FCA | Use only the negative-exposure part for the deduction (`ratioRBTPos = 0`) or compute FBA separately |
| 5 | `fVAEL` magnitude. `fVAEL[k] ÷ Δt_k` should equal the funding spread (about 60–85bp here); the chat's example debug line (`14,1.23e-05,0`) would imply about 1.5bp | Check units against real debug output |

Replacement for `constantWeights`, assuming `fVAEL[m]` is the cost over (t\_m−1, t\_m\] with `fVAEL[0] = 0`:

```fsharp
// C(tau): cumulative spot funding cost over (0, tau], piecewise-linear between grid times.
let cumulCost = (Array.scan (+) 0.0 fVAEL).[1..]          // cumulCost[m] = C(t_m)
let costTo (tau: float<Year>) =
    let tau = max tau 0.0<Year>
    let n = numTB
    if tau >= timeInYears[n-1] then
        let rate = (cumulCost[n-1] - cumulCost[n-2]) / float (timeInYears[n-1] - timeInYears[n-2])
        cumulCost[n-1] + rate * float (tau - timeInYears[n-1])
    else
        let m = timeInYears |> Array.findIndex (fun x -> x > tau)   // m >= 1
        let w = float ((tau - timeInYears[m-1]) / (timeInYears[m] - timeInYears[m-1]))
        cumulCost[m-1] + w * (cumulCost[m] - cumulCost[m-1])

let constantWeights =
    lazy (Array2D.init numTB numTB (fun j k ->
            if k <= j then 0.0
            else costTo (timeInYears[k] - timeInYears[j]) - costTo (timeInYears[k-1] - timeInYears[j])))
```

The zero-DF handling is correct as written: flagged paths are removed before the mean, the regression and the vol, which rules out the earlier hypothesis that dead paths diluted the average.

## Validation

The fix should pass five tests before 385212 is rerun, then go through an engine-wide validation.

### Tests before rerunning 385212

| Test | Pass if |
| --- | --- |
| Column 0, any curve | Constant equals Forward exactly (a difference points to the k = j convention) |
| Flat funding curve | Constant equals Forward at every column, within interpolation error |
| Grid density (weekly, monthly, quarterly) | Lifetime results within about 1% |
| Zero-coupon test trade | Lifetime ≈ 30mm (see below) |
| 385212 rerun | Run in both Constant and Forward styles; compare like with like against the prover |

### Zero-coupon test trade

The bank pays a single fixed X = 100mm at T = 5y, uncollateralised, to a test counterparty with the prepay's profile (RR\_35, unrated, unsecured) in its own netting set. Book it in Calypso UAT as a zero-coupon fixed-vs-fixed swap: pay leg fixed, ZC frequency, 1/1 day count, N × R = X; receive leg fixed at 0%; no principal exchange.

With flat r = 4% and s = 50bp, the closed form is:

```latex
\mathrm{FBA}_t = X \, e^{-r(T-t)} \, s \, (T-t)
```

Capital\_t = (1 − tax) × FBA\_t, because the put is deep in the money (d = 1 ÷ (σ\_r × (T − t) × √t) ≥ 23 at a 1% rate vol), so rate volatility can stay on.

| t (years) | FBA | Capital (after tax) | Op-FVA RWA |
| --- | --- | --- | --- |
| 0 | 2.047mm | 1.504mm | 13.08mm |
| 1 | 1.704mm | 1.253mm | 10.89mm |
| 2 | 1.330mm | 0.978mm | 8.50mm |
| 3 | 0.923mm | 0.678mm | 5.90mm |
| 4 | 0.480mm | 0.353mm | 3.07mm |
| 5 | 0 | 0 | 0 |

Expected lifetime RWA is about 30.0mm (2.29 × spot): 32.4mm if the 1.08 FVA multiplier applies, 29.0mm with counterparty survival weighting. The old indexing would show about 7.6mm.

### Engine-wide validation

1. **Contain.** Floor Op-FVA at the prover's figure for new pricing, log the incident, and find the release that introduced `fVAEL[k − j]`.
2. **Analytic test set.** Zero-coupon liability, bullet and amortising payer swaps, a sold option with premium, a prepay replica, and an asset-side trade (Op-FVA ≈ 0).
3. **Invariance tests.** Grid density, path count and seed, valuation date ±1 day, notional ×2 linearity, split-trade additivity, zero-DF counts.
4. **Production checks.** Option value ≥ |E\[FVA\_t\]| at every bucket; E\[FVA\_0\] ties to booked FVA; lifetime ÷ spot RWA not far below the MTM-weighted remaining life (385212: engine 0.64y vs about 3.7y); no constant per-step decay.
5. **Book scan.** Run the lifetime-to-spot check on every Constant-style netting set and rank by gap to size the hurdle and capital impact on deals already priced.
6. **Sister profiles.** Check the default and CVA lifetime functions for the same index-offset pattern.
7. **Close out.** Fix, rerun the test set, get model validation sign-off, reprice flagged deals, tell the desks, remove the floor.

## Exposed scenarios and open items

The understatement grows with the size of the FBA and the number of grid steps between a bucket and maturity.

- **Most exposed:** large uncollateralised liabilities on the Constant style: prepays and upfront-received structures, options sold to clients for a premium, off-market swaps where the bank received cash, zero-coupon and back-loaded payer swaps (including inflation ZC), and funding-motivated or collateral-offset trades.
- **Long tenor, slow run-off:** bullet or back-loaded profiles over 3 years on a dense near-term grid lose the most.
- **Incremental pricing:** Test and Base are both distorted, so the difference can be wrong in either direction against a large existing netting set.
- **Least exposed:** two-way low-threshold CSAs, cleared trades, trades under one year, and netting sets where the bank is owed (net FCA, put out of the money).

### Open items

- [ ] Which curve style does the prover use, and which should OSFI capital use?
- [ ] t0 anchor: prover E\[FVA\_0\] 4.04mm vs engine 4.63mm vs booked FVA 4.58mm (LiTES) / 4.61mm (pricer).
- [ ] Align inputs between the two runs: maturity 19-Nov-33 vs 2034-02-27, valuation date, 5y CDS 120 vs 118.
- [ ] Default RWA breaks the other way (lifetime 3.68mm vs 31.47mm): reconcile separately, likely the full-notional vs two-period EAD question.
- [ ] Confirm the tax rate LiTES uses (prover implies 26.4%).
- [ ] Prover housekeeping: `#VALUE!` in Message, `#DIV/0!` in RWA average life, and the "Test regression" column showing formula text.
