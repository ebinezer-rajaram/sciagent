# The evidence wall, and falsification-seeking design

P1 deliverable for SPEC §2.4 and Q6. This note states the evidence-wall proposition,
proves it with explicit and tight constants, derives v1's 0/112 as a corollary,
and sets out the falsification-seeking design (FSD) criterion, including its own blind
spot. `scripts/evidence_wall_check.py` checks every inequality numerically. That script
is a guard against algebra slips, not a proof. Logarithms are natural (nats). v1's code
reported bits, which are nats divided by $\ln 2$.

## 1. Setup

- **Entertained set.** $H_e=\{h_1,\dots,h_K\}$ carries posterior weights
  $w\in\Delta_K$. Hypotheses with $w_k=0$ are dropped, so every $w_k>0$. Write
  $H(w)=-\sum_k w_k\log w_k\le\log K$.
- **Design.** A design is a pair $(e,d)$: an experiment $e$ and a diagnostic $d$. Its
  outcome $Y$ lives in a measurable space $\mathcal Y$. Under $h_k$, $Y$ has law
  $p_k=p(\cdot\mid h_k,e,d)$. The mixture predictive is $p_w=\sum_k w_kp_k$.
- **Truth.** $h^{*}$ generates the data and gives $Y$ the law $p^{*}$. It may lie
  outside $H_e$. Nothing below assumes $h^{*}\in H_e$.
- **Divergences.** $\mathrm{KL}(p\|q)=\int\log\frac{dp}{dq}\,dp$.
  $\mathrm{TV}(p,q)=\sup_A|p(A)-q(A)|$. Squared Hellinger is
  $\mathrm{He}^2(p,q)=1-\int\sqrt{dp\,dq}\in[0,1]$, and $\mathrm{He}$ is a metric.

The **expected information gain** is the mutual information between the hypothesis
index and the outcome, when $H\sim w$ and $Y\mid H=k\sim p_k$:

$$\mathrm{EIG}(e,d)=I(Y;H)=\sum_k w_k\,\mathrm{KL}(p_k\,\|\,p_w).\tag{0}$$

(0) holds because $I(Y;H)=\mathbb E_H\,\mathrm{KL}(P_{Y\mid H}\|P_Y)$. For discrete $Y$
it equals $H(p_w)-\sum_kw_kH(p_k)$, which is the form v1's `boed.py` computed. Note that
$w_kp_k\le p_w$, so $p_k\ll p_w$ and every term is finite.

## 2. The proposition

**Proposition 1 (evidence wall).** Let $\varepsilon=\max_{k,l}\mathrm{TV}(p_k,p_l)$ and
$\kappa=\max_{k,l}\mathrm{KL}(p_k\|p_l)$.

- **(a) Divergence bound.**
  $\displaystyle\mathrm{EIG}\le\sum_{k,l}w_kw_l\,\mathrm{KL}(p_k\|p_l)\le\Big(1-\sum_kw_k^2\Big)\kappa.$
- **(b) Total-variation bound.**
  $\displaystyle\mathrm{EIG}\le\sum_k w_k\log\tfrac1{w_k}\cdot\frac{\mathrm{TV}(p_k,p_w)}{1-w_k}\le\varepsilon\,H(w)\le\varepsilon\log K.$
  By Pinsker's inequality, $\mathrm{EIG}\le\sqrt{\kappa/2}\,H(w)$ as well.
- **(c) Truth-independence.** EIG is a functional of $(w;p_1,\dots,p_K)$ alone. Fix $w$
  and the $p_k$. Then any two truths $h^{*},h^{**}$ give the same EIG, however different
  $p^{*}$ and $p^{**}$ are. Combining this with (b), every design with
  $\varepsilon\le\varepsilon_0$ has $\mathrm{EIG}\le\varepsilon_0\log K$. That bound
  holds uniformly over the truth and over every history that could have produced $w$.
- **(d) Realised version.** Suppose $e^{-\eta}\le dp_k/dp_l\le e^{\eta}$ for all
  $k,l$. Then the observed $y$ moves every posterior log-odds $\log(w_k/w_l)$ by at
  most $\eta$, whatever $y$ is and whatever generated it.

The truth enters EIG only through $w$, that is, through data already seen. The outcome
that this design would produce under $h^{*}$ appears nowhere. Meanwhile the evidence
that this outcome carries against the whole entertained set,
$\mathbb E_{p^{*}}\log\frac{dp^{*}}{dp_w}(Y)=\mathrm{KL}(p^{*}\|p_w)$, is not bounded by
any function of $\varepsilon$. Take $p_k\equiv p_0$ for every $k$, so EIG is 0, and let
$\mathrm{KL}(p^{*}\|p_0)$ be as large as you like. That gap is the wall.

*Proof.* (a) KL is convex in its second argument, so
$\mathrm{KL}(p_k\|\sum_lw_lp_l)\le\sum_lw_l\mathrm{KL}(p_k\|p_l)$. Average over $w_k$.
The diagonal terms vanish, and $\sum_{k\ne l}w_kw_l=1-\sum w_k^2$.

(b) Fix $k$ and put $M=1/w_k>1$ and $r=dp_k/dp_w$. Then $0\le r\le M$ because
$w_kp_k\le p_w$, and $\int r\,dp_w=1$. Let $\phi(r)=r\log r-r+1$. This function is
convex with $\phi(1)=0$ and $\phi(0)=1$, and
$\mathrm{KL}(p_k\|p_w)=\int\phi(r)\,dp_w$ because $\int(1-r)\,dp_w=0$. On $\{r\ge1\}$ the
chord from 1 to $M$ gives $\phi(r)\le\frac{r-1}{M-1}\phi(M)$. On $\{r<1\}$ the chord from
0 to 1 gives $\phi(r)\le 1-r$. Both $\int(r-1)_+dp_w$ and $\int(1-r)_+dp_w$ equal
$\tau:=\mathrm{TV}(p_k,p_w)$, so

$$\mathrm{KL}(p_k\|p_w)\le\tau\Big(\frac{\phi(M)}{M-1}+1\Big)=\tau\,\frac{M\log M}{M-1}=\tau\,\frac{\log(1/w_k)}{1-w_k}.$$

TV is convex and $\mathrm{TV}(p_k,p_k)=0$, so
$\tau\le\sum_{l\ne k}w_l\mathrm{TV}(p_k,p_l)\le(1-w_k)\varepsilon$. Multiply by $w_k$ and
sum to get $\varepsilon H(w)$. The Pinsker form follows from
$\mathrm{TV}\le\sqrt{\mathrm{KL}/2}$.

(c) Read it off (0).

(d) Bayes' rule gives $\log\frac{w_k(y)}{w_l(y)}=\log\frac{w_k}{w_l}+\log\frac{dp_k}{dp_l}(y)$. $\square$

**Tightness.**

1. *(b) is attained, for every $w$ and every $\varepsilon\in[0,1]$.* Take the erasure
   channel: with probability $\varepsilon$, $Y=H$; otherwise $Y=\star$. Then
   $\mathrm{TV}(p_k,p_l)=\varepsilon$ for $k\ne l$, and
   $I(Y;H)=H(w)-(1-\varepsilon)H(w)=\varepsilon H(w)$. So no bound of the form
   $f(\varepsilon)H(w)$ with $f(\varepsilon)<\varepsilon$ holds.
2. *The constant 1 in (a) cannot be lowered.* Take $w=(1-\delta,\delta)$,
   $p_1=\mathrm{Bern}(e)$ and $p_2=\mathrm{Bern}(\tfrac12)$. As $\delta\to0$ the ratio
   EIG/bound tends to
   $\mathrm{KL}(p_2\|p_1)/(\mathrm{KL}(p_2\|p_1)+\mathrm{KL}(p_1\|p_2))$. This limit
   tends to 1 as $e\to0$, because $\mathrm{KL}(p_1\|p_2)\to\log2$ while
   $\mathrm{KL}(p_2\|p_1)\to\infty$. The approach is logarithmic in $1/e$; the script
   prints 0.717, 0.850 and 0.925 at $e=10^{-2},10^{-4},10^{-8}$.
3. *In the regime the wall is about, (a) is loose by exactly a factor of 2.* When the
   $p_k$ are close, $\mathrm{KL}\approx\tfrac12\chi^2$. The identity
   $\sum_{k,l}w_kw_l(a_k-a_l)^2=2\sum_kw_k(a_k-\bar a)^2$ then gives
   $\mathrm{EIG}=\tfrac12\sum_{k,l}w_kw_l\mathrm{KL}(p_k\|p_l)\,(1+o(1))$. For a
   Gaussian shift family the script prints a ratio of 0.500.

**Remarks.**

1. *Parameter uncertainty.* If each $h_k$ carries a parameter posterior, $p_k$ is the
   parameter-averaged predictive and everything above holds verbatim. For a continuum of
   hypotheses, $H(w)$ can be infinite, so use (a).
2. *Lookahead does not help.* An adaptive policy that chooses designs as a functional of
   $(w,\{p_k\})$, such as $T$-step BOED, depends on the truth only through realised
   outcomes. Its total information $I(Y_{1:T};H)$ is again a functional of $H_e$.
3. *Coarsening.* Binning $Y$, as v1 did, can only lower EIG, by the data-processing
   inequality. An EIG *estimated* from $M$ simulations per hypothesis is different: its
   plug-in estimate on $C$ cells has positive bias of order $(C-1)(K-1)/M$. For an
   invariant design, the estimated EIG is therefore estimator noise.

## 3. Corollary: v1's `size_gap_correlation` and the 0/112

v1's statistic was the Pearson correlation
$T=\mathrm{corr}\big((m_i)_{i<n},(g_i)_{i<n}\big)$. Here $g_i=t_{i+1}-t_i$ is the gap
*after* event $i$, which gives $N=n-1$ pairs.

**Condition (U).** Under $h$, conditional on the arrival times (and on $n$), the marks
are exchangeable. iid marks independent of the arrival process is the special case.
Every member of v1's closed set satisfied (U): DECISIONS 2026-08-16 records "every
closed-set member is uncoupled", and the mechanisms modulate only arrival rates.

**Lemma 2 (exact).** Assume (U) and condition on $n$, the gaps and the multiset of
marks. Then $T$ follows the permutation law of
$\sum_ia_ib_{\pi(i)}/(\|a\|\|b\|)$, where $a$ and $b$ are the centred gaps and marks.
That law has **mean 0 and variance exactly $1/(N-1)$**, for *any* gap sequence.

*Proof.* Conditionally, every arrangement of the marks is equally likely, and the
denominators are invariant under permutation. Take $\sum a=\sum b=0$. Then
$\mathbb E\,b_{\pi(i)}=0$, $\mathbb E\,b_{\pi(i)}^2=\|b\|^2/N$, and, for $i\ne j$,
$\mathbb E\,b_{\pi(i)}b_{\pi(j)}=-\|b\|^2/(N(N-1))$. Because
$\sum_{i\ne j}a_ia_j=-\|a\|^2$, the second moment is
$\|a\|^2\|b\|^2(\frac1N+\frac1{N(N-1)})=\|a\|^2\|b\|^2/(N-1)$. $\square$
(The script checks this exactly over all $7!$ pairings for heavy-tailed and clustered
gaps.)

**Corollary 3.** Suppose every $h\in H_e$ satisfies (U). Then, under every $h\in H_e$:

- **Exact.** $\mathbb E_hT=0$ and $\mathrm{Var}_hT=\mathbb E_h[1/(N-1)]$. The variance
  depends on $h$ only through the law of the event count, and not at all when $n$ is
  fixed.
- **Approximate.** The remaining dependence on $h$ sits in the higher moments of the
  permutation law, through the empirical shape of the gaps. Stationary ergodic gaps with
  finite variance satisfy Noether's condition, $\max_i a_i^2/\|a\|^2\to0$. The
  Wald–Wolfowitz–Hájek permutation CLT then gives $\sqrt{N-1}\,T\Rightarrow N(0,1)$
  under every $h\in H_e$. So
  $\varepsilon_N=\max_{k,l}\mathrm{TV}(\mathcal L_k(T),\mathcal L_l(T))\to0$, and
  Proposition 1(b) gives $\mathrm{EIG}(T)\le\varepsilon_N\log K\to0$. I expect
  $\varepsilon_N=O(N^{-1/2})$ from an Edgeworth expansion but have not proved the rate.
  Nothing here relies on it.
- Under $h^{*}$ (size-excited arrivals), a large mark shortens the next gap, so
  $\mathbb E_{h^{*}}T<0$ and stays bounded away from 0 as $N$ grows. Proposition 1(c)
  says this shift does not enter EIG at all.

**The v1 numbers** (DECISIONS 2026-08-16: 200 replicates of 512 events, so $N=511$).

- **Means.** The closed-set means lie in $[-0.0020,+0.0006]$. The standard error is
  about 0.003, so these are consistent with the exact mean 0.
- **SDs.** Lemma 2 predicts an SD of $1/\sqrt{510}=0.0443$. Null, Hawkes,
  poisson_mixture and seasonality read 0.0444–0.0462, within one or two SEs. (The SE of
  an SD over 200 draws is about 0.0022.)
- **regime_switching** reads 0.0392, about 2.3 SE low. By Lemma 2, either that is chance
  or that mechanism's logs had a different event count; 0.0392 corresponds to
  $N\approx650$. I could not tell which without re-running v1.
- **S11's truth** reads $-0.1342\pm0.0308$.

Treat each row as Gaussian and weight the closed set uniformly:

| quantity (nats) | value |
|---|---|
| EIG over the closed set, using the recorded moments | 0.0035 |
| bounds: (a) 0.0074; (b) $\varepsilon H(w)$, with $\varepsilon=0.082$ | 0.132 |
| EIG using Lemma 2's exact moments (identical predictives) | 0 |
| $\mathrm{KL}(p^{*}\|p_w)$, one observation | **4.63** |
| EIG if S11 were entertained, $w=1/6$ each | 0.377 (vs $\log6=1.79$) |

The diagnostic carries about 1,300 times more evidence against the entertained set than
it carries about it. Even the small 0.0035 comes mostly from SD differences that Lemma 2
attributes to event-count or sampling effects. This matches the record:

- BOED never selected the design, on any of the twelve scenarios (DECISIONS
  2026-08-16).
- It was observed in 0 of 112 briefs (RESULTS, finding 3).
- Adding S11's mechanism to the entertained set would raise its EIG about a
  hundredfold, to 0.377 nats. How that would rank against the other designs was not
  measured.

**What is and is not derived here.** Proposition 1 and Lemma 2 explain why one-step EIG
ranks this design at or near the bottom. They do not prove that this ranking alone
produced 0/112. That count also depends on budgets and on the other designs' EIG, and
v1 records both. The wall is relative to $H_e$. In v2, once some entertained feature set
contains `Excite(·, Mark(size), ·)` with weight on a different coupling, (U) fails and
$T$ is no longer walled.

## 4. Falsification-seeking design

For a design $(e,d)$ with outcome statistic $S$, define three predictive laws:

- $q^{\rm par}_{e,d}$: the law of $S$ under the framework-fitted best entertained model
  (the MAP structure with certified $\hat\theta,\hat\psi$, SPEC §2.2). The posterior
  mixture $p_w$ works identically.
- $q^{\rm np}_{e,d}$: the law of $S$ when the linear process with the Wiener–Hopf kernel
  estimates (SPEC §2.3) is simulated under $e$, with the intensity clipped at 0.
- $q^{*}_{e,d}$: the law of $S$ under the truth.

$$\mathrm{FSD}(e,d)=\mathrm{He}^2\big(q^{\rm par}_{e,d},\,q^{\rm np}_{e,d}\big),\qquad (e,d)^{\rm FSD}=\arg\max\mathrm{FSD}.$$

**Why Hellinger.** It is a bounded metric, which the proof below needs, and it is
defined on the binned outcomes the EIG machinery already uses. It also controls testing.
Write $\mathrm{BC}=1-\mathrm{He}^2$. For any test between $q^{\rm par}$ and $q^{*}$ on
$m$ replications, the minimal sum of the two error rates is at most
$\mathrm{BC}^m$, because $\mathrm{BC}$ tensorises and $1-\mathrm{TV}\le\mathrm{BC}$. It is
also at least $1-\sqrt{1-\mathrm{BC}^{2m}}$. So a small He² means no affordable
experiment can reject the model, and a large He² means a few replications will.

**Cheap surrogate.** With $z^2=(\mu_{\rm par}-\mu_{\rm np})^2/(\sigma^2_{\rm par}+\sigma^2_{\rm np})$,
Gaussian predictives give
$\mathrm{He}^2=1-\sqrt{2\sigma_1\sigma_2/(\sigma_1^2+\sigma_2^2)}\,e^{-z^2/4}$. With equal
variances this is $1-e^{-z^2/4}$, which is monotone in $z^2$.

**Proposition 4 (FSD is not walled).** By the triangle inequality for He,

$$\big|\sqrt{\mathrm{FSD}(e,d)}-\mathrm{He}(q^{\rm par},q^{*})\big|\le\mathrm{He}(q^{\rm np},q^{*}).$$

Now assume (A): the model-free predictive is consistent on $(e,d)$, meaning
$\mathrm{He}(q^{\rm np}_n,q^{*})\to0$ in probability as the data grow. Then
$\mathrm{FSD}\to\mathrm{He}^2(q^{\rm par},q^{*})$. This limit is the discrepancy between
the best entertained model and the truth on that design, which is the oracle's
"telling diagnostic" criterion of SPEC §4.2 with He² as the divergence. Unlike
Proposition 1(c), the limit depends on $h^{*}$. Hold $H_e$, $w$ and $q^{\rm par}$ fixed
and change only the truth: the limiting FSD changes.

*When does (A) hold?* Let $\mathcal L$ be the class of stationary, stable (spectral
radius $<1$) linear Hawkes processes on the channels Wiener–Hopf uses: arrivals, and
arrivals weighted by the mark. If $h^{*}\in\mathcal L$ and the law of $S$ under $e$ is
continuous in the kernels, then:

- the Wiener–Hopf estimates are consistent for the kernels (Bacry & Muzy 2016), and
- $q^{\rm np}\to q^{*}$ by continuity.

A truth with excitation affine in the mark under the identity link lies in
$\mathcal L$. S11's truth `Excite(ExpK, Mark(size), all)` is of that form, provided the
intensity stays positive without clipping. For v1's diagnostic, this predicts
$\mathrm{FSD}\to0.79$ against a fitted Hawkes model, where EIG gave 0.0035. The script's
Hellinger table uses the recorded moments.

*Finite samples.* When the parametric model is right,
$\mathrm{FSD}\approx\mathrm{He}^2(q^{\rm par},q^{\rm np})$ is pure Wiener–Hopf estimation
noise, which is larger than parametric noise. So FSD needs a null calibration: its
distribution when the truth is the fitted model, from a parametric bootstrap. The
calibration is a negative control, and it must be run beside the §6.4 positive control
before Q6 is read.

**Proposition 5 (FSD's own wall).** Let $\ell(h^{*})$ be the linear process whose
kernels are the large-sample limit of the Wiener–Hopf estimator under $h^{*}$, and let
$q^{\rm lin}$ be its predictive under $e$. The estimator is a functional of the
empirical first- and second-order statistics (intensities and cross-covariance
densities). So $\ell(h^{*})$ depends on $h^{*}$ only through its observational
second-order structure $\Lambda(h^{*})$. Then:

- **(i) Second-order invariance.** $\lim\mathrm{FSD}=\mathrm{He}^2(q^{\rm par},q^{\rm lin})$
  is a functional of $(q^{\rm par},\Lambda(h^{*}))$. Two truths with equal second-order
  structure have the same limiting FSD on every design. This mirrors Proposition 1(c),
  with $\mathcal L$ in the role of $H_e$.
- **(ii) Quantitative form.**
  $|\sqrt{\lim\mathrm{FSD}}-\mathrm{He}(q^{\rm par},q^{*})|\le\mathrm{He}(q^{\rm lin},q^{*})$.
  FSD can under-report the model's error by up to the truth's own departure from its
  linear shadow, and that much is attained. If the parametric fit agrees with the shadow
  on $(e,d)$, so that $q^{\rm par}=q^{\rm lin}$, then $\mathrm{FSD}\to0$ however large
  $\mathrm{He}(q^{\rm par},q^{*})$ is.

*Proof.* Use the triangle inequality as in Proposition 4, with $q^{\rm lin}$ in place
of the limit of $q^{\rm np}$. $\square$

The truths this bites are the ones SPEC §2.3 names:

- `Gate(·, Above(·))` and other thresholds,
- strongly curved exp or softplus links,
- interventional designs $e$, such as forced marks or deleted events. On these, a
  nonlinear truth's response departs from linear extrapolation even when its
  observational second-order structure is reproduced exactly.

That set is where structure proposal can beat both EIG and FSD, and where Q6 must
measure FSD's miss rate.

**Remark (one wall, two vantage points).** Take $H_e'=\{\text{par},\text{np}\}$ with
$w=(\tfrac12,\tfrac12)$. Then EIG is the Jensen–Shannon divergence
$\mathrm{JS}(q^{\rm par},q^{\rm np})\le\log2\cdot\mathrm{TV}$, by Proposition 1(b), and
it vanishes exactly when FSD does. FSD is therefore EIG after one hypothesis has been
added to the entertained set: a hypothesis that is not written in the proposal
vocabulary. Each criterion is blind to whatever its reference set cannot express.

## 5. What this does and does not claim

**Claims (proved above):**

- Over any entertained set, EIG depends only on the entertained predictives and weights.
  It is at most $\varepsilon H(w)$, and this bound is attained.
- An invariant diagnostic has near-zero EIG however strongly the truth moves it. This
  holds for adaptive and lookahead policies too.
- v1's diagnostic is invariant across the closed set: exactly in mean and variance (up
  to the law of the event count), and asymptotically in law.
- FSD's large-sample limit sees the truth wherever the model-free estimate is
  consistent.
- FSD's blind spot is exactly the truth's departure from its second-order linear shadow.

**Does not claim:**

- That EIG is a bad criterion. It is the right criterion for discriminating within
  $H_e$, and the wall is a statement about what that question cannot ask.
- That the wall alone caused 0/112. Budgets and competing designs also mattered.
- That FSD improves outcomes. That is Q6's empirical question, and it is reportable only
  after the §6.4 positive control and FSD's null calibration.
- Any rate for $\varepsilon_N$ in Corollary 3. Only the limit is proved.
- Consistency of Wiener–Hopf beyond the stated class $\mathcal L$.
- That the regime_switching SD anomaly is explained. It is attributed to event count or
  chance, unverified.
- Anything about what an agent with free analysis (AG-o) can see. That is Q7.
