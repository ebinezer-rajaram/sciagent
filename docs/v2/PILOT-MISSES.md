# Pilot search misses: the section 10 note

**Status: reviewed.** Red team 2026-11-05, two rounds, each ready with fixes (`docs/v2/redteam/2026-11-05_pilot-misses.md`, `-round2.md`). Its fixes are in the generator's wording and in the section added by hand at the end, which limits several lines above: read that section with them. Round 2's fixes were not read again by a red team.

Stored readings, the stored verdict and PILOT.md's generated sections stay as they are. A corrected reading is never presented as the pilot's reading.

```sh
uv run python scripts/p3_report/miss_note.py --records <records directory>
```

Files read:

- `decision.json`: sha256 11857cb0572b56e4ddb2b046f6d27a95dbc6dca29fcc4e4967b335e8284da101
- `units.jsonl`: sha256 0d379e1c70641c6d00d8e46e7762c9b8bad0566c5d554bcbeec9bacc93733f78
- `timing.jsonl`: sha256 d3657a63cff0d2e52a973ae3bf19891e471d6b7444f39c9c3c8432bdd5dedfe5

## 1. Label and hypotheses

| Field | Value |
|---|---|
| Label | GO_SURVIVES_FOUND_MISSES |

Label line: GO_SURVIVES_FOUND_MISSES | H2 falsified=True | H3 falsified=True | M=25 rows, 25 fits | uncertified_better on identity 0 | group A 5 of 6, group B True, group D True | max|delta|=0.021980616852384116 | exact 310, searched 140, unchecked 1, capped 52 | s1_status=not_estimable | group C id-24000: 150 cases completed, 1 found losses, R1 run on 1; 0 of 1 caught | group C id-large: 150 cases completed, 7 found losses, R1 run on 7; 5 of 7 caught | group C large-exp: 150 cases completed, 11 found losses, R1 run on 11; 7 of 11 caught | group C large-softplus: 150 cases completed, 4 found losses, R1 run on 4; 4 of 4 caught | groups_a_b: 8 searched fits, group A 5 of 6, group B caught | large-exp: 9 searched fits, 7 of 11 caught | large-softplus: 41 searched fits, 4 of 4 caught | id-24000: 5 searched fits, 0 of 1 caught | id-large: 17 searched fits, 5 of 7 caught | no_control: 51 searched fits, no control | rows per gate: too_easy: exact 105, searched 83, exhaustive_uncertified 0, not_applicable 22; too_hard: exact 214, searched 134, exhaustive_uncertified 0, not_applicable 82; C1: exact 71, searched 27, exhaustive_uncertified 0, not_applicable 22; C2: exact 104, searched 76, exhaustive_uncertified 0, not_applicable 0; C3: exact 40, searched 30, exhaustive_uncertified 0, not_applicable 0 | coverage: rows exact 441, searched 143, unchecked 1, not applicable 85 | The computed GO survives substitution of the 23 misses found (N counts baseline rows, the ones the verdict reads). Of the 397 fits the verdict reads, 184 are exact over their certified grid points, 131 are searched and unvalidated, 0 are exhaustive but uncertified and were not checked, and 82 are outside the check (B-np, B-lib's out-of-grammar members). Nothing is said about misses on the 131 fits that the search did not find.

> The computed GO survives substitution of the 23 misses found (N counts baseline rows, the ones the verdict reads). Of the 397 fits the verdict reads, 184 are exact over their certified grid points, 131 are searched and unvalidated, 0 are exhaustive but uncertified and were not checked, and 82 are outside the check (B-np, B-lib's out-of-grammar members). Nothing is said about misses on the 131 fits that the search did not find.

- `provisional`: false
- `partial`: false
- `exit_code`: 0
- `end`: complete
- Did the computed GO survive: It survived the misses found, in section 2.7's wording
- Next: The go/no-go goes to the owner with that wording and the per-gate line. No longer held for this check
- Stop note: none
- Byte failures: none
- tier 1 fits missing: 0
- tier 2 fits missing: 0
- S2 rows missing: 0

| Hypothesis | State |
|---|---|
| H1 | not falsified |
| H2 | falsified |
| H3 | falsified |
| H4 | not falsified |

H1: group A 5 of 6, group B caught true, group D caught true.
H3: largest |Δ| among the misses found: 0.021980616852384116.
H4: `v.outcome`: go.

## 2. Controls

For group D the best value is R2's, as section 2.9 defines that group.

| Group | Case | Caught | fit/5 | Known better value | R1's own best |
|---|---|---|---|---|---|
| A | 0, softplus, 103648458 | true | -247.14024253195458 | -245.81534542479207 | -245.81534542479207 |
| A | 0, softplus, 1074 | true | -266.91305606627486 | -266.9090837823743 | -266.9090837823743 |
| A | 0, softplus, 2006407976 | true | -254.99001416930616 | -252.90808070621256 | -252.3214798433397 |
| A | 0, softplus, 3273957339 | true | -261.54594896669107 | -261.2740684974972 | -261.2740684974972 |
| A | 0, softplus, 358866363 | false | -249.53786861477403 | -248.9435521602365 | -249.53786861477403 |
| A | 2, exp, 1499614403 | true | -284.6126940541121 | -275.61199838143034 | -275.0416305823561 |
| B | dev-015, 3 | true | -781.9771383733416 | -777.4488821620253 | -776.3435689779456 |
| D | 0, softplus, 103648458 | true | -247.14024253195458 | -245.81534542479207 | -245.81534542479207 |

Group C, per stratum:

| Stratum | completed | found_losses | r1_run | caught | not_run | uncertified | sim_failed | Control state |
|---|---|---|---|---|---|---|---|---|
| id-24000 | 150 | 1 | 1 | 0 | 0 | 0 | 0 | 0 of 1 caught |
| id-large | 150 | 7 | 7 | 5 | 0 | 0 | 0 | 5 of 7 caught |
| large-exp | 150 | 11 | 11 | 7 | 0 | 0 | 0 | 7 of 11 caught |
| large-softplus | 150 | 4 | 4 | 4 | 0 | 0 | 0 | 4 of 4 caught |

## 3. Classes

| Class | Rows |
|---|---|
| Coordinate path | 267 |
| Exhaustive, `certified` = 1 | 317 |
| Exhaustive, `certified` = 0 | 1 |
| No ψ search: B-np | 60 |
| No ψ search: B-lib, out-of-grammar member won | 22 |
| No submission | 3 |
| Failed | 0 |

## 4. Coverage

| Coverage | Rows | Fits |
|---|---|---|
| Exact (R2 or exhaustive) | 441 | 310 |
| Searched (R1 only) | 143 | 140 |
| Unchecked | 1 | 1 |
| Capped | not recorded | 52 |
| Not applicable | 85 | not recorded |

Rows each §7.1 gate reads:

| Gate | Rows |
|---|---|
| C1 | exact 71, exhaustive_uncertified 0, not_applicable 22, searched 27 |
| C2 | exact 104, exhaustive_uncertified 0, not_applicable 0, searched 76 |
| C3 | exact 40, exhaustive_uncertified 0, not_applicable 0, searched 30 |
| too_easy | exact 105, exhaustive_uncertified 0, not_applicable 22, searched 83 |
| too_hard | exact 214, exhaustive_uncertified 0, not_applicable 82, searched 134 |

Searched tier 1 fits by stratum, and that stratum's control result:

| Stratum | Searched fits | Control |
|---|---|---|
| groups_a_b | 8 | group A 5 of 6, group B caught |
| id-24000 | 5 | 0 of 1 caught |
| id-large | 17 | 5 of 7 caught |
| large-exp | 9 | 7 of 11 caught |
| large-softplus | 41 | 4 of 4 caught |
| no_control | 51 | no control |

## 5. Misses

M = 25 rows, 25 fits. M is a lower bound (section 2.2).

`uncertified_better`: 0. `uncertified_better` on the identity link: 0.
largest |Δ| among the misses found: 0.021980616852384116.
`within_quadrature` rows: none.

| System | Miss rows |
|---|---|
| AG-o-S/anon | 1 |
| AG-o-S/named | 1 |
| B-rand | 5 |
| B-sparse | 11 |
| B-sym | 1 |
| ORACLE | 2 |
| planted-hint | 4 |

| Truth | Seed | System | Training gain (nats) | Δ | Stored reading | Corrected reading | Evidence class | `stored_uncertified` | `within_quadrature` | Stored quadrature error | Chosen quadrature error |
|---|---|---|---|---|---|---|---|---|---|---|---|
| dev-000 | 3 | B-rand | 13.174054539866802 | 0.0016550285598102565 | -0.8956228006087511 | -0.8939677720489408 | searched | false | false | 9.094947017729282e-13 | 6.45741238258779e-11 |
| dev-001 | 2 | B-sparse | 1.985718368284779 | -0.004416807903595554 | -0.7561755272710561 | -0.7605923351746516 | searched | false | false | 7.912603905424476e-11 | 1.540502125862986e-08 |
| dev-002 | 1 | B-rand | 0.5093178041779538 | -0.0016785037418576287 | -0.7305779494648454 | -0.7322564532067031 | exact | false | false | 0.0 | 0.0 |
| dev-003 | 1 | B-sparse | 8.504351193561888 | 0.001485603324517526 | -0.6833957481735441 | -0.6819101448490266 | searched | false | false | 1.8189894035458565e-12 | 1.3642420526593924e-12 |
| dev-005 | 2 | B-sparse | 1.4370022726225216 | -0.0013809333056241657 | -0.7417706707462617 | -0.7431516040518859 | searched | false | false | 3.334207576699555e-09 | 1.1305019143037498e-09 |
| dev-005 | 2 | planted-hint | 12.609563860746675 | 0.004750551722496721 | -0.7812508407644392 | -0.7765002890419425 | searched | false | false | 9.817085810936987e-09 | 5.117726686876267e-09 |
| dev-007 | 1 | B-sparse | 3.8219325834849087 | 0.0017903816480551171 | -0.6108312067566528 | -0.6090408251085977 | searched | false | false | 6.381519597198348e-07 | 8.14125087345019e-08 |
| dev-008 | 2 | B-sparse | 0.281838079232557 | 0.00031295111729334746 | -0.7679368477984524 | -0.767623896681159 | searched | false | false | 1.2732925824820995e-11 | 2.419255906715989e-10 |
| dev-008 | 3 | B-sparse | 1.8429534185124794 | -0.00101387555907273 | -0.7870181068324993 | -0.788031982391572 | searched | false | false | 1.0231815394945443e-10 | 0.0 |
| dev-009 | 3 | B-sparse | 2.262852386211307 | -0.0030205240795113664 | -0.6746966143440666 | -0.677717138423578 | searched | false | false | 2.2282620193436742e-11 | 5.902620614506304e-10 |
| dev-009 | 3 | ORACLE | 0.3138182280474666 | 0.001243064872885169 | -0.6569627722507172 | -0.655719707377832 | searched | false | false | 6.552454578923061e-08 | 6.2795243138680235e-06 |
| dev-011 | 2 | B-sym | 0.6952849416311437 | -0.0021245213321573475 | -0.7068156702374473 | -0.7089401915696046 | searched | false | false | 0.0 | 0.0 |
| dev-012 | 1 | B-sparse | 1.7357795143675503 | -0.0010600789976611402 | -0.4728295910350702 | -0.47388967003273136 | searched | false | false | 7.863491191528738e-09 | 2.6057023205794394e-10 |
| dev-012 | 3 | B-rand | 4.5188760864548385 | 0.001572470278697613 | -0.49563637818009343 | -0.4940639079013958 | searched | false | false | 2.241904439870268e-10 | 1.6955973478616215e-06 |
| dev-012 | 3 | B-sparse | 31.804379342223 | 0.021980616852384116 | -0.45676328714512804 | -0.4347826702927439 | searched | false | false | 5.838955985382199e-10 | 6.521986506413668e-09 |
| dev-013 | 3 | planted-hint | 0.41773179247115877 | -0.0012110332211411334 | -0.6361762314880431 | -0.6373872647091843 | searched | false | false | 1.3642420526593924e-12 | 1.3642420526593924e-12 |
| dev-014 | 3 | planted-hint | 1.3201561917126128 | -0.008311281188174147 | -0.7237377567403828 | -0.7320490379285569 | searched | false | false | 9.094947017729282e-13 | 9.094947017729282e-13 |
| dev-015 | 1 | B-rand | 1.1095379417083677 | 0.0004622447506321681 | -0.6051533171425155 | -0.6046910723918834 | searched | false | false | 1.7102119272749405e-05 | 0.0008482784760417417 |
| dev-015 | 3 | ORACLE | 1.105313184079705 | -0.0009626355836831468 | -0.6256227862063536 | -0.6265854217900367 | searched | false | false | 5.773258635599632e-07 | 4.993353286408819e-07 |
| dev-015 | 3 | planted-hint | 38.050957481202545 | 0.016698630449300644 | -0.7311748941635043 | -0.7144762637142037 | exact | false | false | 1.037601577991154e-07 | 1.262524165213108e-07 |
| dev-017 | 1 | AG-o-S/named | 14.872258716980014 | 0.000788868457216596 | -0.667970481478136 | -0.6671816130209194 | exact | false | false | 2.723436409723945e-08 | 1.7721658878144808e-06 |
| dev-017 | 1 | B-sparse | 2.0218287561722263 | 0.0003462590826799339 | -0.5984968706524536 | -0.5981506115697737 | searched | false | false | 1.4452702544076601e-05 | 5.077106834505685e-06 |
| dev-019 | 3 | AG-o-S/anon | 25.732101426137433 | 0.010502875263185052 | -0.9664049790758689 | -0.9559021038126838 | exact | false | false | 9.094947017729282e-13 | 4.547473508864641e-13 |
| dev-019 | 3 | B-rand | 0.2560006727990185 | 0.0023773806103813966 | -0.9430086798131164 | -0.940631299202735 | searched | false | false | 4.5929482439532876e-11 | 0.0 |
| dev-019 | 3 | B-sparse | 3.681947756153022 | -0.0021620113123181905 | -0.9347194517582746 | -0.9368814630705928 | searched | false | false | 2.773958840407431e-11 | 5.4569682106375694e-12 |

### ORACLE search misses

Search misses on ORACLE: 2; units with the stored search below truth ψ: 1.

## 6. Verdict on corrected readings

| Statistic | Stored | Corrected |
|---|---|---|
| Outcome | go | go |
| too_easy flag | false | false |
| too_easy recovery B-lib | 0.0 | 0.0 |
| too_easy recovery B-rand | 0.0 | 0.0 |
| too_easy B-sparse out-of-dictionary close | 0.2333333333333333 | 0.2333333333333333 |
| too_hard flag | false | false |
| too_hard close | B-lib 0.0, B-np 0.0, B-rand 0.1, B-sparse 0.4666666666666666, B-sym 0.2833333333333333, B-sym@10F 0.9, planted-hint 0.4333333333333333 | B-lib 0.0, B-np 0.0, B-rand 0.1, B-sparse 0.4666666666666666, B-sym 0.2833333333333333, B-sym@10F 0.9, planted-hint 0.4333333333333333 |
| C1 kept | dev-000, dev-001, dev-002, dev-003, dev-004, dev-005, dev-006, dev-007, dev-008, dev-009, dev-010, dev-011, dev-012, dev-013, dev-014, dev-015, dev-016, dev-017, dev-018, dev-019 | dev-000, dev-001, dev-002, dev-003, dev-004, dev-005, dev-006, dev-007, dev-008, dev-009, dev-010, dev-011, dev-012, dev-013, dev-014, dev-015, dev-016, dev-017, dev-018, dev-019 |
| C1 dropped | none | none |
| C1 underfit passed | true | true |
| C1 ORACLE at -inf | none | none |
| C2 passed | true | true |
| C2 wins | 18 | 18 |
| C2 p value | 4.76837158203125e-06 | 4.76837158203125e-06 |
| C2 median | 0.024488052663725595 | 0.024767803287368533 |
| C3 passed | true | true |
| C3 median gap | 0.0 | 0.0 |
| Reasons | none | none |
| Inconclusive | none | none |

Clauses moved: none.

## 7. Secondary and exploratory analyses

### S1

`s1_status`: not_estimable. Misses R1 had found: 4, of 4 on fits where R2 ran (distinct fits).

### S2

S2 rows: differs 2, done 51, link_mismatch 0, same 49.
`v2` outcome: go.

### S3

| Measure | Value |
|---|---|
| Training gain, nats | max 38.050957481202545, median 1.985718368284779, min 0.2560006727990185, n 25 |
| Training gain, per event | max 0.02000576103112647, median 0.0009625391993624716, min 0.00013278043194969838, n 25 |
| Δ | max 0.021980616852384116, median 0.0003462590826799339, min -0.008311281188174147, n 25 |
| Δ above 0 | 14 |
| Δ below 0 | 11 |
| Moves to or from -inf | 0 |
| Near ties | 0 |
| `uncertified_better` | 0 |
| `uncertified_better`, identity link | 0 |
| `capped` fits | 52 |
| Completed starts | max 48.0, median 48.0, min 6.0, n 255 |
| `stored_uncertified` rows | 1 |
| ORACLE search misses | 2 |
| ORACLE stored search below truth ψ | 1 |

### S4

Each statistic's limit: not recorded. Each §7.1 statistic stored and corrected is in section 6 of this note.
Corrected gaps above +0.01: 0.

| Truth | Seed | System | gap_stored | gap_corrected | gap_closed_stored | gap_closed_corrected | t4_flag_stored | t4_flag_corrected | similarity_stored | similarity_corrected |
|---|---|---|---|---|---|---|---|---|---|---|
| dev-000 | 3 | B-rand | -0.06383152421750582 | -0.06217649565769556 | 0.2665905282105082 | 0.2856064241447945 | false | false | 0.7406069496895731 | 0.8232126462876886 |
| dev-001 | 2 | B-sparse | -0.003360506654441875 | -0.007777314558037429 | 0.9672394822081143 | 0.9241814172232332 | false | false | 0.9766771515820635 | 0.9790488693156092 |
| dev-002 | 1 | B-rand | -0.03176513498601119 | -0.03344363872786882 | -0.0513088244071116 | -0.10686110826777807 | false | false | 0.9141753752686463 | 0.917873742265843 |
| dev-003 | 1 | B-sparse | -0.05138413919587448 | -0.04989853587135695 | 0.7364210408403817 | 0.7440415592362963 | false | false | 0.9338749800249682 | 0.9240120517871746 |
| dev-005 | 2 | B-sparse | -0.07244107449054771 | -0.07382200779617187 | 0.6881524296710475 | 0.6822077263494337 | false | false | 0.9186415390681217 | 0.9371990958291241 |
| dev-005 | 2 | planted-hint | -0.1119212445087252 | -0.10717069278622848 | 0.5181964318766045 | 0.5386468189367235 | false | false | 0.9171726919757841 | 0.8875579390300284 |
| dev-007 | 1 | B-sparse | -0.009128799070453164 | -0.007338417422398047 | 0.9114524060377279 | 0.9288187634288779 | false | false | 0.9888125502290425 | 0.9822911571369822 |
| dev-008 | 2 | B-sparse | -0.001319376570455555 | -0.0010064254531622074 | 0.9907175723339233 | 0.9929193289622757 | false | false | 0.9738131774544889 | 0.9770074233219165 |
| dev-008 | 3 | B-sparse | -0.0035736359609461754 | -0.004587511520018905 | 0.9731420997613182 | 0.9655222501410442 | false | false | 0.9546073778779726 | 0.972040830562381 |
| dev-009 | 3 | B-sparse | -0.017733842093349383 | -0.02199743104574592 | 0.6746378143019054 | 0.605413030822116 | false | false | 0.9671964715559231 | 0.9741485360982193 |
| dev-009 | 3 | ORACLE | 0.0 | 0.0 | 1.0 | 1.0 | false | false | 0.9744849526981805 | 0.9695162897634423 |
| dev-011 | 2 | B-sym | -0.06675783728532836 | -0.0688823586174857 | 0.540206222432237 | 0.5255736080672626 | false | false | 0.9294068623246096 | 0.8834004453141189 |
| dev-012 | 1 | B-sparse | -0.018843911781730838 | -0.019903990779391978 | 0.8988948369258816 | 0.8932070868890826 | false | false | 0.9722431680769307 | 0.975838487266162 |
| dev-012 | 3 | B-rand | -0.07952779417345668 | -0.07795532389475907 | 0.5629447150063854 | 0.5715864289246819 | false | false | 0.8826276479267683 | 0.92323680474744 |
| dev-012 | 3 | B-sparse | -0.04065470313849129 | -0.018674086286107172 | 0.7765768175617965 | 0.8973741421022092 | false | false | 0.9511329544378566 | 0.9648445806140177 |
| dev-013 | 3 | planted-hint | 0.0010996111858995183 | -0.00011142203524161509 | 1.0061664615492356 | 0.999375161234384 | false | false | 0.9814503009146908 | 0.9853192779112065 |
| dev-014 | 3 | planted-hint | -0.02005335195283353 | -0.028364633141007678 | 0.42886663783596285 | 0.19215558922636192 | false | false | 0.9601535955057798 | 0.9412454048024099 |
| dev-015 | 1 | B-rand | -0.1363490971510143 | -0.13588685240038212 | 0.29989422984223585 | 0.30226769782922436 | false | false | 0.9373155504593662 | 0.9445311061320073 |
| dev-015 | 3 | ORACLE | 0.0 | 0.0 | 1.0 | 1.0 | false | false | 0.965814816817907 | 0.969326528853621 |
| dev-015 | 3 | planted-hint | -0.10555210795715075 | -0.08789084192416696 | 0.4637610654278053 | 0.5512916149234123 | false | false | 0.8465692108312783 | 0.9402096387350597 |
| dev-017 | 1 | AG-o-S/named | -0.12048540528221807 | -0.11969653682500148 | 0.5266739540328996 | 0.5297730180794361 | false | false | 0.9649150478683153 | 0.9563305352720468 |
| dev-017 | 1 | B-sparse | -0.0510117944565357 | -0.05066553537385576 | 0.7996005332658994 | 0.8009608095757246 | false | false | 0.9773616527580119 | 0.9675711685295478 |
| dev-019 | 3 | AG-o-S/anon | -0.05685605458486942 | -0.04635317932168437 | 0.1915171532036985 | 0.3408661461003326 | false | false | 0.9152774643333458 | 0.9343567205613111 |
| dev-019 | 3 | B-rand | -0.03345975532211698 | -0.031082374711735583 | 0.5242083110858022 | 0.5580142347967132 | false | false | 0.8891787318618336 | 0.9177374297608779 |
| dev-019 | 3 | B-sparse | -0.0251705272672752 | -0.02733253857959339 | 0.6420796397324037 | 0.6113362286154813 | false | false | 0.9495074935023029 | 0.9403993545073649 |

### Exploratory

**Exploratory: no decision reads this table.** Per-system tallies are shown as counts only. No difference between systems is claimed, and no control supports one.

Fits read from `units.jsonl`: 255. `reference_failed` records, which hold no result and are not in these tables: 0.

| Link | Fits read | Fits with a miss | Miss rows | Miss rows per system |
|---|---|---|---|---|
| exp | 47 | 6 | 6 | AG-o-S/anon 1, B-sparse 2, ORACLE 1, planted-hint 2 |
| identity | 73 | 2 | 2 | B-rand 1, B-sym 1 |
| softplus | 135 | 17 | 17 | AG-o-S/named 1, B-rand 4, B-sparse 9, ORACLE 1, planted-hint 2 |

| Number of features | Fits read | Fits with a miss | Miss rows | Miss rows per system |
|---|---|---|---|---|
| 1 | 4 | 1 | 1 | B-rand 1 |
| 2 | 78 | 5 | 5 | AG-o-S/anon 1, B-rand 2, ORACLE 2 |
| 3 | 90 | 5 | 5 | AG-o-S/named 1, B-rand 2, planted-hint 2 |
| 4 | 83 | 14 | 14 | B-sparse 11, B-sym 1, planted-hint 2 |

| G | Fits read | Fits with a miss | Miss rows | Miss rows per system |
|---|---|---|---|---|
| G ≤ 14,400 | 115 | 4 | 4 | AG-o-S/anon 1, AG-o-S/named 1, B-rand 1, planted-hint 1 |
| 14,400 < G ≤ 24,000 | 16 | 2 | 2 | ORACLE 1, planted-hint 1 |
| 24,000 < G ≤ 3,456,000 | 73 | 6 | 6 | B-rand 2, B-sparse 1, B-sym 1, ORACLE 1, planted-hint 1 |
| G > 3,456,000 | 51 | 13 | 13 | B-rand 2, B-sparse 10, planted-hint 1 |

| `period` slots | Fits read | Fits with a miss | Miss rows | Miss rows per system |
|---|---|---|---|---|
| 0 | 60 | 5 | 5 | B-sparse 1, B-sym 1, ORACLE 2, planted-hint 1 |
| 1 | 63 | 4 | 4 | AG-o-S/named 1, B-rand 2, planted-hint 1 |
| 2 or more | 132 | 16 | 16 | AG-o-S/anon 1, B-rand 3, B-sparse 10, planted-hint 2 |

Miss rows on fits with 2 or more `period` slots, by whether two slots hold one period:

| At the stored point | At the chosen point | Miss rows | Per system |
|---|---|---|---|
| yes | yes | 10 | B-rand 1, B-sparse 7, planted-hint 2 |
| yes | no | 0 | none |
| no | yes | 1 | B-rand 1 |
| no | no | 5 | AG-o-S/anon 1, B-rand 1, B-sparse 3 |

The same (truth, seed, structure) across systems. Fits with a miss on one system: 25. On two or more systems: 0.

| Truth | Seed | Structure | Link | Features | G | `period` slots | Systems with a miss | Other systems on this fit, with their call |
|---|---|---|---|---|---|---|---|---|
| dev-000 | 3 | `link=softplus; Product(Excite(GammaK, Above(size), all), Periodic) + Product(Periodic, Product(Excite(ExpK, One, all), Periodic))` | softplus | 2 | 24,000 < G ≤ 3,456,000 | 2 or more | B-rand | none |
| dev-001 | 2 | `link=softplus; Excite(ExpK, Above(size), all) + Gate(Excite(ExpK, Above(size), all), PhaseWindow) + Gate(Excite(GammaK, Above(size), sign=+), PhaseWindow) + Gate(Excite(GammaK, Above(size), sign=-), PhaseWindow)` | softplus | 4 | G > 3,456,000 | 2 or more | B-sparse | none |
| dev-002 | 1 | `link=identity; Excite(PowerK, One, all) + Gate(Gate(Excite(GammaK, One, all), LastMarkAbove(size)), PhaseWindow)` | identity | 2 | G ≤ 14,400 | 1 | B-rand | none |
| dev-003 | 1 | `link=exp; Excite(ExpK, Above(size), sign=-) + Product(Periodic, Periodic) + Gate(Excite(GammaK, One, all), PhaseWindow) + Gate(Excite(PowerK, ExpOf(size), all), PhaseWindow)` | exp | 4 | G > 3,456,000 | 2 or more | B-sparse | none |
| dev-005 | 2 | `link=softplus; Excite(ExpK, One, all) + Excite(PowerK, One, sign=+) + Product(Periodic, Product(Excite(ExpK, One, all), Excite(PowerK, Above(size), all))) + Gate(Excite(ExpK, One, sign=-), PhaseWindow)` | softplus | 4 | G > 3,456,000 | 2 or more | planted-hint | none |
| dev-005 | 2 | `link=softplus; Excite(GammaK, Mark(sign), all) + Gate(Excite(GammaK, Mark(size), sign=-), PhaseWindow) + Gate(Excite(PowerK, Mark(size), all), PhaseWindow) + Gate(Excite(PowerK, Above(size), all), PhaseWindow)` | softplus | 4 | G > 3,456,000 | 2 or more | B-sparse | none |
| dev-007 | 1 | `link=softplus; Excite(ExpK, Pow(size), all) + Excite(PowerK, Pow(size), all) + Gate(Excite(PowerK, One, sign=-), LastMarkAbove(size)) + Gate(Excite(PowerK, One, all), LastMarkAbove(size))` | softplus | 4 | 24,000 < G ≤ 3,456,000 | 0 | B-sparse | none |
| dev-008 | 2 | `link=softplus; Gate(Excite(ExpK, Pow(size), sign=-), PhaseWindow) + Gate(Excite(ExpK, Above(size), all), PhaseWindow) + Gate(Excite(GammaK, Pow(size), sign=-), PhaseWindow) + Gate(Excite(PowerK, Above(size), all), PhaseWindow)` | softplus | 4 | G > 3,456,000 | 2 or more | B-sparse | none |
| dev-008 | 3 | `link=softplus; Gate(Excite(ExpK, Pow(size), sign=-), PhaseWindow) + Gate(Excite(GammaK, Mark(size), sign=-), PhaseWindow) + Gate(Excite(GammaK, Pow(size), sign=-), PhaseWindow) + Gate(Excite(PowerK, Above(size), all), PhaseWindow)` | softplus | 4 | G > 3,456,000 | 2 or more | B-sparse | none |
| dev-009 | 3 | `link=softplus; Excite(GammaK, One, all) + Excite(GammaK, Pow(size), all) + Gate(Excite(GammaK, One, sign=+), PhaseWindow) + Gate(Excite(GammaK, Above(size), sign=+), PhaseWindow)` | softplus | 4 | G > 3,456,000 | 2 or more | B-sparse | none |
| dev-009 | 3 | `link=softplus; Excite(GammaK, Pow(size), all) + Product(Excite(PowerK, Mark(size), all), Gate(Excite(ExpK, Above(size), sign=+), LastMarkAbove(size)))` | softplus | 2 | 24,000 < G ≤ 3,456,000 | 0 | ORACLE | none |
| dev-011 | 2 | `link=identity; Excite(ExpK, One, all) + Excite(GammaK, One, sign=+) + Excite(GammaK, Above(size), sign=+) + Excite(PowerK, One, all)` | identity | 4 | 24,000 < G ≤ 3,456,000 | 0 | B-sym | none |
| dev-012 | 1 | `link=softplus; Excite(PowerK, Mark(size), sign=+) + Excite(PowerK, Pow(size), sign=+) + Gate(Excite(ExpK, One, sign=-), PhaseWindow) + Gate(Excite(PowerK, Mark(size), sign=+), PhaseWindow)` | softplus | 4 | G > 3,456,000 | 2 or more | B-sparse | none |
| dev-012 | 3 | `link=softplus; Excite(GammaK, ExpOf(size), all) + Excite(PowerK, Mark(size), sign=+) + Product(Gate(Excite(ExpK, Pow(size), sign=-), LastMarkAbove(size)), Gate(Periodic, PhaseWindow))` | softplus | 3 | G > 3,456,000 | 2 or more | B-rand | none |
| dev-012 | 3 | `link=softplus; Excite(PowerK, Mark(size), sign=+) + Gate(Excite(GammaK, One, all), PhaseWindow) + Gate(Excite(PowerK, Mark(size), sign=+), PhaseWindow) + Gate(Excite(PowerK, Above(size), sign=+), PhaseWindow)` | softplus | 4 | G > 3,456,000 | 2 or more | B-sparse | none |
| dev-013 | 3 | `link=softplus; Periodic + Gate(Periodic, PhaseWindow) + Gate(Gate(Periodic, PhaseWindow), PhaseWindow)` | softplus | 3 | 24,000 < G ≤ 3,456,000 | 2 or more | planted-hint | none |
| dev-014 | 3 | `link=exp; Excite(ExpK, One, all) + Excite(GammaK, Mark(size), all) + Excite(GammaK, Above(size), sign=+) + Periodic` | exp | 4 | 14,400 < G ≤ 24,000 | 1 | planted-hint | none |
| dev-015 | 1 | `link=softplus; Excite(ExpK, One, sign=-) + Product(Excite(ExpK, One, all), Excite(ExpK, ExpOf(size), sign=-)) + Gate(Gate(Excite(GammaK, ExpOf(size), sign=-), LastMarkAbove(size)), PhaseWindow)` | softplus | 3 | G > 3,456,000 | 1 | B-rand | none |
| dev-015 | 3 | `link=exp; Excite(ExpK, One, all) + Excite(PowerK, Pow(size), sign=-) + Product(Excite(ExpK, One, all), Excite(ExpK, Mark(size), all))` | exp | 3 | G ≤ 14,400 | 0 | planted-hint | none |
| dev-015 | 3 | `link=exp; Excite(ExpK, One, sign=-) + Product(Excite(ExpK, Pow(size), all), Product(Excite(ExpK, Mark(size), all), Excite(ExpK, Pow(size), all)))` | exp | 2 | 14,400 < G ≤ 24,000 | 0 | ORACLE | none |
| dev-017 | 1 | `link=softplus; Excite(ExpK, Pow(size), sign=-) + Excite(GammaK, Pow(size), sign=-) + Periodic` | softplus | 3 | G ≤ 14,400 | 1 | AG-o-S/named | none |
| dev-017 | 1 | `link=softplus; Product(Periodic, Periodic) + Gate(Excite(ExpK, Pow(size), sign=-), PhaseWindow) + Gate(Excite(GammaK, One, sign=-), LastMarkAbove(size)) + Gate(Excite(PowerK, Mark(size), sign=-), PhaseWindow)` | softplus | 4 | G > 3,456,000 | 2 or more | B-sparse | none |
| dev-019 | 3 | `link=exp; Periodic + Gate(Excite(PowerK, One, all), PhaseWindow)` | exp | 2 | G ≤ 14,400 | 2 or more | AG-o-S/anon | none |
| dev-019 | 3 | `link=exp; Product(Excite(ExpK, Pow(size), sign=+), Excite(GammaK, One, sign=-)) + Gate(Excite(ExpK, One, all), PhaseWindow) + Gate(Excite(PowerK, One, all), PhaseWindow) + Gate(Periodic, PhaseWindow)` | exp | 4 | G > 3,456,000 | 2 or more | B-sparse | none |
| dev-019 | 3 | `link=softplus; Product(Gate(Excite(ExpK, Pow(size), all), PhaseWindow), Gate(Excite(PowerK, One, all), PhaseWindow))` | softplus | 1 | 24,000 < G ≤ 3,456,000 | 2 or more | B-rand | none |

## 8. Not covered and unknown

Fixed words of the pre-registration, whatever M is.

| System | Step that used fit/5 | Handling |
|---|---|---|
| B-rand | BIC over 40 proposals, drawn with no fit result | **Not covered.** It could be redone in principle, but B-rand's recorded CPU was 36.84 hours and the reference is several times fit/5's points, so hundreds of CPU hours (section 8) |
| B-sym, B-sym@10F, planted-hint | BIC selection and the GP's fitness at every generation | **Not covered, and cannot be by substitution.** A better fit changes the population and every later proposal, so it is a different run |
| LLM arms | The agent read fit/5's numbers during its session | **Not covered** (section 2.6) |

Stated plainly: for B-rand, B-sym, B-sym@10F, planted-hint and the LLM arms, the corrected reading answers "given the structure this system submitted, what does a better fit of it score?". It does not answer "what would it have submitted?". The direction of the omitted effect is unknown for scores. For recovery, `exact` depends on the structure only, so substitution moves no system's `exact` flag. A recovery share can move only through which truths are kept: a substituted ORACLE reading can change `kept` (`analysis.py:601`), and recovery is computed over kept truths (`analysis.py:685-689`). That is the only route by which the too-easy clause on recovery can react to this check, for B-rand or B-lib.

**What stays unknown either way:** misses R1 did not find on the 140 searched fits; everything in the not-covered rows of section 2.5; the curves; and sampling noise, since each reading is one held-out log per (truth, seed).

PILOT.md's curve tables therefore stay unchecked, and the report says so.

The 140 is the text's planned count; section 4 of this note gives the final one.

## 9. Cost, records and departures

Sums over the jobs in `timing.jsonl`. A job writes its line when it finishes, so a job that was killed is not here, and the sum of job wall times is not the run's wall time.

| Stage | Jobs | CPU hours | Children's CPU hours | Job wall hours |
|---|---|---|---|---|
| group_a | 6 | 0.060 | 0.000 | 0.061 |
| group_b | 1 | 0.020 | 0.000 | 0.020 |
| group_d | 1 | 0.028 | 0.000 | 0.028 |
| hunt | 623 | 2.297 | 0.000 | 2.307 |
| s2 | 51 | 7.044 | 0.000 | 7.049 |
| tier1 | 195 | 14.401 | 0.000 | 14.414 |
| tier2 | 59 | 1.052 | 0.000 | 1.053 |
| All | 936 | 24.903 | 0.000 | 24.932 |

Lines with no CPU figure: 0.

- Kernel CPU count and run wall time: not recorded in these files.
- Clock stops: cpu_cut 0, cpu_limit 0, cpu_stop false, group_c_cap false, r2_lost 0.
- LEDGER row: not recorded.
- S3 path, as section 2.10 fixes it: s3://delaymargin-results-482913/sciagent/p3-linux/search-misses-3226f9b9521fe3a8c1d2445d1654cd1609b1eb2d/ (whether the copy was made: not recorded).
- `F`: 3226f9b9521fe3a8c1d2445d1654cd1609b1eb2d
- `R`: 5e5a1696d0064d25da8c9a3fbf6d8a420d27c0ad
- Pre-registration sha256: ea71cdea282c9dac13586649b504286e11221958aa5764aba16326a37c972659
- Departures from the pre-registration: not recorded.
- Whether anyone read `journal.jsonl` before the run ended: not recorded.

## Fields not recorded

Fields this note looked for in `decision.json` and did not find:

- none

Asked for by section 10 or the sections it draws on, and carried by no record this note reads:

- Coverage: capped rows, and fits not applicable (`coverage` has `capped_fits` and `not_applicable_rows` only)
- Section 2.1's split of each class into baseline and LLM rows
- The rows behind each clause that moved (`clauses_moved` has the clause and its consequence only)
- S4: each §7.1 statistic's limit
- The kernel's CPU count and the run's wall time (`cpu.json`, which this note does not read)
- The LEDGER row, whether the S3 copy was made, and departures from the pre-registration
- Whether anyone read `journal.jsonl` before the run ended (section 9)

## Added by hand (session 31)

Everything above is the generator's output, apart from the status line. These lines fill what it marks "not recorded", and answer the red team's report (`docs/v2/redteam/2026-11-05_pilot-misses.md`, verdict ready with fixes: the label, and every number it recomputed, held). The values below are the red team's unless a line says otherwise; I reran `c_controls.py` and `i_more.py` from `.cache/drafts/s31/rt/` and they printed the same counts.

### How far this reaches

- This is no new worst-case figure for fit/5. The check gives a lower bound on M and the largest |Δ| among the misses found; no bound of any kind is given on misses it did not find (prereg, section 1).
- **The control rates in section 4 are not recall for the fits beside them.** Group C's R1 cases have 158 to 357 events, and pilot fits have 1,797 to 2,327. 22 of the 23 R1 runs were capped, and `id-large` got 4 to 6 starts.
- 13 of the 25 misses, and the largest Δ, sit in the 51 no-control fits. 38 of those are capped, with as few as 6 starts.
- R1 alone fell short of fit/5's own value on 5 fits, the worst by 6.9 nats on dev-008 s2 with all 48 starts and no cap, in the stratum shown as 4 of 4 caught.
- S1 is `not_estimable` at 4 of 4. R2 beat R1 on 0 of 115 fits, which says nothing above G = 14,400.
- dev-015 s3 is not blind, and H1 depends on group B (prereg, opening section).
- The per-system counts in section 5 support no comparison between systems. Exposure differs: 10 of B-sparse's 11 misses are at G > 3,456,000.

### The gates

- **The too-easy line in section 4 understates what is unchecked.** `too_easy: exact 105, searched 83` includes B-lib and B-rand recovery rows, whose `exact` flag substitution cannot move. The statistic substitution moves directly is B-sparse out of dictionary; recovery shares could move only through `kept` (section 8 above), and `kept` did not change here. On its 30 rows, 5 are exact (1 by R2, 4 exhaustive), 3 are searched in a stratum with a control, and 22 are searched with no control. It also reads ORACLE on the same 30 units: 9 exact, 21 searched. 7 of the 30 B-sparse rows carry a found miss, the largest |Δ| 0.00442, and none changes the count. 7 of the 30 close the gap; a share of 0.7 needs 21, so 14 more. The 14th smallest shift needed is 0.02266 (dev-013 s3), and 13 rows need 0.02198 or less.
- Do not read the gates as far from their limits on the strength of this check. The largest Δ found, 0.02198, is on dev-012 s3, an in-dictionary truth and so not one of those 30 rows. It is above the C1 (0.0217), C2 (0.0148) and C3 (0.0100) figures of the withdrawn stress test and just under its 0.0227. Those figures assumed a shift on every row in one direction, and B-sparse's found Δs have mixed signs, so this is neither a bound nor a threat estimate.
- C2's second condition: CI 0.014792505887358457 to 0.030626581033161644 stored, 0.0134072923559961 to 0.03036450265337871 corrected, low end above 0 in both. C1's smallest margin is 0.0266 at dev-002 (SE 0.0049, so 0.0217 above the SE), stored and corrected alike.
- Each gate's limit, for S4: too-easy share ≥ 0.7; too-hard every system < 0.1; C1 margin > SE, zero underfit flags, no -inf; C2 p < 0.05 and CI low > 0; C3 median ≥ -0.01.

### Uncertified points

`uncertified_better` counts only points with a score, so failed solves are not covered by its 0. On the two fits enumerated, every uncertified point was a solver failure with L = -inf, and a retry through Clarabel failed too. ORACLE dev-010 s1's failed point has a certified neighbour 0.36 nats below the chosen L, so a better fit there cannot be ruled out. 49 of the 70 points were not enumerated.

Section 2.2's per-fit count, 70 points on 17 identity fits (`system, G: count`). On searched fits the count is over the points R1 visited, 1,221 to 2,994 per fit, not over G:

| Class | Fits |
|---|---|
| Exact, 46 points on 10 fits | dev-012 s2 B-sym 3,600: 20; dev-013 s2 AG-o-S/anon 12,000: 10; dev-001 s1 B-rand 13,824: 8; dev-010 s2 B-rand 14,400: 2; dev-003 s1 AG-o-S/anon 1,440: 1; dev-017 s1 AG-c-S/named 720: 1; dev-017 s1 AG-o-S/anon 4,800: 1; dev-018 s2 B-sparse 576: 1; dev-010 s1 ORACLE 2,304: 1; dev-013 s2 AG-c-S/anon 600: 1 |
| Searched, 24 points on 7 fits | dev-019 s1 B-rand 345,600: 8; dev-011 s2 planted-hint 20,736: 5; dev-010 s1 B-rand 16,588,800: 5; dev-008 s3 B-rand 115,200: 2; dev-011 s2 B-sym 41,472: 2; dev-003 s1 AG-c-S/named 21,600: 1; dev-019 s1 B-sym 172,800: 1 |

### The solver warnings

The unit's log holds 276 `Solution may be inaccurate` lines from `fit_solve.py:591`, which is Clarabel, so only identity fits can raise them. Each process warned once, by Python's once-per-location filter, so 276 counts processes and not solves; the true count cannot be recovered (two fits rerun with `always` gave 63 and 240). A warning does not mean a bad final solve. All 25 miss rows' stored and chosen solves reproduce L, gap and held-out bytes with status `optimal` and no warning, and the largest sum of gaps on any row is 1.5e-7 against a smallest gain of 0.256 nats. H1 reads no identity case, and dropping both identity substitutions still gives `go`. So the warnings touch no miss row, no Δ and not the label; failed solves can only hide misses on identity fits, which M's lower bound already allows.

### Fields the records do not carry

| Item | Value |
|---|---|
| Classes, baseline + LLM rows | Coordinate 205 + 62; exhaustive certified 143 + 174; exhaustive uncertified 0 + 1, which is dev-005 s3 AG-o-S/named (identity, G = 180, reading -inf) |
| Capped | 53 rows (52 baseline) on 52 fits: 51 tier 1 searched fits, plus dev-013 s2 AG-o-S/anon, G = 12,000, exact after R2 |
| Not applicable | 82 fits (60 B-np, 22 B-lib); the other 3 of the 85 rows have no fit |
| ORACLE search misses | dev-015 s3: search gain 5.63357, reading gain 1.10531, truth ψ kept, Δ -0.00096; dev-009 s3: 0.31382, ψ source search, Δ +0.00124 |
| `stored_uncertified` row | dev-003 s1 AG-c-S/named, searched, call none; fit-level flag false, its own point certified |
| S2 rows that differ | dev-019 s2 exp to softplus, -0.86227 to -0.87085, Δ -0.00858, out of dictionary; dev-016 s2 softplus to exp, -0.78476 to -0.78992, Δ -0.00516, in dictionary |
| Rows behind a moved clause | None: no clause moved |

On dev-019 s2 the reference's exp fit scored about -3e11 on validation against about -471 for R0's, and that flipped the link: a better training fit is not always a better model.

How the counts reconcile. Fits, all: 310 exact (195 exhaustive + 115 by R2), 140 searched, 1 unchecked, as in the label line. Fits the verdict reads: 184 exact (119 + 65), 131 searched, 0 unchecked, 82 outside, as in the fixed statement. The stratum table in section 4 is tier 1 only and sums to 131; the 9 tier 2 fits add 2 to `groups_a_b`, 1 to `id-24000` and 6 to `large-softplus`. "Capped 52" includes the one exact fit above.

### Cost and records

- Kernel CPU count: 25.41 CPU hours (`cpu.json`: `current_usec` 91468211483, `earlier_usec` 0; the unit's own line reads 1d 1h 24min 28.795s). Ceiling 84, stop at 78, neither reached.
- Run wall time: 12.83 hours (`cpu.json`: `current_wall_s` 46182.26), 11:59:49Z on 2026-11-04 to 00:49:38Z on 2026-11-05. One unit, no resume.
- LEDGER: the launch row of 2026-11-04, hours filled at 25.41, $0.
- S3 copy: made 2026-11-05 at 04:17Z, seven files. The red team hashed the six data files equal to local; `driver.lock` is empty at both ends.
- `journal.jsonl` before the run ended: the journal entries of sessions 29 and 30 record status read from `systemctl` and `cpu.json` only, and a search of the session transcripts found no command naming a record file's contents during the run. Reads outside Claude sessions cannot be excluded.
- Departures from the pre-registration: none in the launch line, guards, stage order or budget (red team). The first version of this note lacked items sections 2.2, 4, 6 and 10 ask for; they are above, and the LOG records it (2026-11-05).
- Not rerun by anyone: R1, R2 and the hunt on the reserved roots, so byte identity of `units.jsonl` on a rerun is not shown, and the 317 exhaustive rows rest on the code argument.
