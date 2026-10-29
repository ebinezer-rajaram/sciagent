"""System and opening prompts for the AG-c and AG-o arms (SPEC §4.0, §4.4, §5).

One template serves both naming conditions. Channel names, diagnostic names
and every time-valued number are filled in from the :class:`AgentView`, so the
named and anonymised prompts differ in exactly the names and units, and in
nothing else.

What "named" means (SPEC §5). The named condition keeps the environment's own
channel names (``size``, ``sign``) and diagnostic names, and native time
units. Neither condition tells a domain story: no prompt says what the events
are, where they come from or what field this is. A story would make the two
conditions differ by more than names, and Q2 asks what the *names* are worth
(a domain prior carried by meaningful identifiers); a narrative would be a
second, stronger manipulation mixed into the same contrast. The anonymised
prompts are checked for forbidden terms by ``tests/investigation``.

Variants (P4 Δ6). :data:`PROMPT_VARIANTS` are ``v1`` and ``link-neutral``. They
differ in two places only: the grammar's examples (:func:`examples`; v1 starts
at the identity link and stays at depth 2 or less, link-neutral rotates the
identity, exp and softplus links and adds one depth-3 example) and the scoring
paragraph (:func:`_scoring`; link-neutral states it as held-out prediction and
structural distance, without v1's "a simpler structure beats a larger one").

Beliefs (P4 Δ4). The prompt asks for a ``belief`` before every experiment and
before ``submit`` (the tools enforce it), for at least one prediction under a
rival per experiment, and for a ``stop_reason`` at ``submit``.

Policy mode (P4 Δ11). With a ``policy`` (:data:`POLICY_NAMES`, AG-c only) the
framework chooses every experiment and the agent only decides whether and when
to run one. Two places change, and only then: the ``run_experiment`` line of
the tools section, and a paragraph after the experiments section saying who
chooses (:func:`_policy_paragraph`). The text never names the rule, so the
three policies show the agent the same prompt, and no policy number is ever in
it. Without a policy every text is byte-identical to the text before policy
mode existed.
"""

from __future__ import annotations

from typing import Final, Literal

from sciagent.core.errors import SciAgentError
from sciagent.glm.grammar import MAX_DEPTH, MAX_FEATURES, ChannelKind, ChannelSpec
from sciagent.glm.grids import PSI_GRIDS
from sciagent.glm.interventions import (
    DEFAULT_HORIZON,
    MAX_CLAMP_RATE,
    MAX_FORCED_EVENTS,
    MAX_HORIZON,
)
from sciagent.investigation.view import PSI_TIME_POWER, AgentView
from sciagent.investigation.world import MAX_EVENTS

__all__ = [
    "MAX_DESIGN_FILE_BYTES",
    "POLICY_NAMES",
    "PROMPT_VARIANTS",
    "STOP_REASONS",
    "WORK_PREFIX",
    "Arm",
    "PolicyName",
    "PromptError",
    "PromptVariant",
    "as_policy",
    "as_variant",
    "budget_line",
    "examples",
    "opening_prompt",
    "system_prompt",
]

type Arm = Literal["AG-c", "AG-o"]
#: The system prompt's variants (P4 Δ6): ``v1``, the text the pilot used plus
#: the P4 additions, and ``link-neutral`` (see :func:`examples` and
#: :func:`_scoring`). Everything else is the same text in both.
type PromptVariant = Literal["v1", "link-neutral"]
PROMPT_VARIANTS: Final[tuple[PromptVariant, ...]] = ("v1", "link-neutral")
#: The experiment-choice policies of policy mode (P4 Δ11, SPEC §4.2).
type PolicyName = Literal["random", "EIG", "FSD"]
POLICY_NAMES: Final[tuple[PolicyName, ...]] = ("random", "EIG", "FSD")
#: Why the agent submits (P4 Δ4); ``submit`` takes exactly one of these.
STOP_REASONS: Final = ("confident", "budget", "evidence_unlikely_to_change", "other")


class PromptError(SciAgentError):
    """A prompt cannot be rendered as asked (an unknown variant)."""


def as_variant(value: object) -> PromptVariant:
    """``value`` as a :data:`PromptVariant`; :class:`PromptError` otherwise."""
    if value == "v1":
        return "v1"
    if value == "link-neutral":
        return "link-neutral"
    raise PromptError(
        f"unknown prompt variant {value!r}; the variants are "
        f"{', '.join(PROMPT_VARIANTS)}"
    )


def as_policy(value: object) -> PolicyName | None:
    """``value`` as a :data:`PolicyName`, or None; :class:`PromptError` otherwise."""
    if value is None:
        return None
    for name in POLICY_NAMES:
        if value == name:
            return name
    raise PromptError(
        f"unknown policy {value!r}; the policies are {', '.join(POLICY_NAMES)}"
    )


def check_policy_arm(arm: object, policy: PolicyName | None) -> None:
    """Policy mode exists for the constrained tier only (P4 Δ11: AG-c-S)."""
    if policy is not None and arm != "AG-c":
        raise PromptError(f"policy {policy!r} runs on AG-c only, not on {arm!r}")


#: The sandbox's writable directory, as the agent's code sees it.
WORK_PREFIX: Final = "/work/"
#: Largest design file ``run_experiment`` reads from ``/work`` (P4 Δ3).
MAX_DESIGN_FILE_BYTES: Final = 1_000_000


def budget_line(
    experiments_left: int, experiments: int, fits_left: int, fits: int
) -> str:
    """The line every tool result ends with (P4 Δ12); counts are unit-free."""
    return (
        f"budget left: {experiments_left} of {experiments} experiments, "
        f"{fits_left} of {fits} fits"
    )


_KIND_TEXT: Final[dict[ChannelKind, str]] = {
    ChannelKind.POSITIVE: "positive real",
    ChannelKind.REAL: "real",
    ChannelKind.SIGN: "binary, values -1 or +1",
}


def num(value: float) -> str:
    """The one number format every agent-visible text uses."""
    return f"{value:.6g}"


def _channel_lines(view: AgentView) -> str:
    lines = []
    for spec in view.channels:
        line = f"- `{spec.name}`: {_KIND_TEXT[spec.kind]}"
        if spec.kind is not ChannelKind.SIGN:
            line += (
                f"; standardised as z = ({spec.name} - {num(spec.location)}) / "
                f"{num(spec.scale)}"
            )
        lines.append(line)
    return "\n".join(lines)


def _grid_lines(view: AgentView) -> str:
    lines = []
    for name, values in PSI_GRIDS.items():
        shown = ", ".join(num(view.psi_value(name, v)) for v in values)
        unit = {1: " (time)", -1: " (per unit time)", 0: ""}[PSI_TIME_POWER[name]]
        lines.append(f"- {name}{unit}: {shown}")
    return "\n".join(lines)


def _sign_channel(channels: tuple[ChannelSpec, ...]) -> str:
    for spec in channels:
        if spec.kind is ChannelKind.SIGN:
            return spec.name
    return "s"


def _example_channel(channels: tuple[ChannelSpec, ...]) -> str:
    for spec in channels:
        if spec.kind is not ChannelKind.SIGN:
            return spec.name
    return channels[0].name if channels else "c"


#: An example longer than this (with its indent) is wrapped at its top-level
#: `` + ``, one feature per line.
_EXAMPLE_WIDTH: Final = 76


def examples(view: AgentView, variant: str = "v1") -> tuple[str, ...]:
    """The grammar's example structures for ``variant``, in ``view``'s names.

    One line each, unwrapped. Every example parses in its view, and the
    link-neutral variant has exactly one at depth :data:`MAX_DEPTH`.
    """
    s = _sign_channel(view.channels)
    c = _example_channel(view.channels)
    two = (
        f"Excite(PowerK, Mark({c}), all) "
        f"+ Gate(Excite(ExpK, One, {s}=+), LastMarkAbove({c}))"
    )
    if as_variant(variant) == "v1":
        return (
            "link=identity; Excite(ExpK, One, all)",
            "link=exp; Periodic + Trend",
            f"link=identity; {two}",
        )
    return (
        "link=exp; Excite(ExpK, One, all)",
        "link=softplus; Periodic + Trend",
        f"link=identity; {two}",
        f"link=softplus; Gate(Product(Periodic, Excite(ExpK, One, all)), "
        f"LastMarkAbove({c}))",
    )


def _example_lines(text: str) -> list[str]:
    line = f"    {text}"
    if len(line) <= _EXAMPLE_WIDTH:
        return [line]
    head, _, body = text.partition("; ")
    first, *rest = body.split(" + ")
    indent = " " * (4 + len(head) + 2)
    return [f"    {head}; {first}", *(f"{indent}+ {f}" for f in rest)]


def _example_block(view: AgentView, variant: str) -> str:
    return "\n".join(
        line for text in examples(view, variant) for line in _example_lines(text)
    )


def _scoring(variant: str) -> str:
    """The paragraph that says how the submission is scored."""
    if as_variant(variant) == "v1":
        return """\
You are scored after you submit, on fresh data you never see: how well your
submitted structure, fitted by the framework, predicts that data, and how close
it is to the true mechanism. A simpler structure that is right beats a larger
one that merely fits."""
    return """\
You are scored after you submit on held-out prediction and structural
distance: how well your submitted structure, fitted by the framework, predicts
fresh data you never see, and how close its structure is to the true mechanism."""


def _grammar(view: AgentView, variant: str) -> str:
    s = _sign_channel(view.channels)
    return f"""\
## The model class

A structure specifies the conditional intensity (the instantaneous event rate
given the past):

    lambda(t) = g( theta0 + sum_k theta_k * phi_k(t) )

You choose the link g and the features phi_k. The framework fits every number:
the coefficients theta and each feature's shape parameters psi (profiled over
the fixed grids below). When a structure has at most 512 psi combinations, every
one is tried and the fit is the best on the grid; otherwise a deterministic
local search is used, which can miss the best grid point. For the chosen psi,
theta is optimal, with a convex-optimality certificate. The language has no
numeric literals.

    Structure := [link=identity; | link=exp; | link=softplus;] Feature + ...
                 (0 to {MAX_FEATURES} features; `null` is the intercept-only model)
    Feature   := Excite(Kernel, MarkFn, Source)
                   = sum over past events j in Source of
                     MarkFn(mark_j) * Kernel(t - t_j)
               | Periodic     sin(2 pi t / P) and cos(2 pi t / P); psi: period
               | Trend        t / T, T the horizon of the dataset
               | Product(Feature, Feature)   products of every column pair
               | Gate(Feature, Cond)   the feature, zero while Cond is false
    Kernel    := ExpK      beta * exp(-beta u); psi: exp_rate
               | PowerK    ((p - 1) / c) * (1 + u / c)^(-p); psi: power_c, power_p
               | GammaK    gamma density, shape k, mean mu;
                           psi: gamma_shape, gamma_mean
    MarkFn    := One | Mark(ch) | Pow(ch) | ExpOf(ch) | Above(ch)
                   Mark: the raw value (not z); Pow: (value / location)^a (positive
                   channels); ExpOf: exp(a z); Above: 1 if z > q else 0;
                   psi: a = pow_exponent or exp_coef, q = above_z
    Cond      := LastMarkAbove(ch)   the most recent event had z > q (psi: above_z)
               | PhaseWindow         sin(2 pi t / P - phase) >= 0
                                     (psi: period, phase)
    Source    := all | {s}=+ | {s}=-   (only past events with that value of `{s}`)

Kernels are normalised densities, so under the identity link an Excite
coefficient is the expected number of events each source event triggers per
unit of MarkFn. Nesting depth (counting Excite, Periodic, Trend, Product and
Gate) is at most {MAX_DEPTH}. Examples:

{_example_block(view, variant)}

Shape-parameter grids (time-valued ones in the units of your data):
{_grid_lines(view)}
"""


def _run_experiment_line(
    view: AgentView, experiments: int, policy: PolicyName | None
) -> str:
    if policy is not None:
        return f"""\
- `run_experiment()`: run the next experiment. The framework chooses it (see
  "Who chooses the experiments" below); you decide whether and when to run one.
  Budget: {experiments} experiments in total. Its dataset id is `e0`, `e1`, ...
  in order."""
    horizon = num(view.time(DEFAULT_HORIZON))
    return f"""\
- `run_experiment(intervention, horizon)`: one new realisation of the process,
  from empty history on [0, horizon] (default {horizon}), under an intervention.
  Budget: {experiments} experiments in total. Its dataset id is `e0`, `e1`, ...
  in order."""


def _tools(
    view: AgentView,
    arm: Arm,
    experiments: int,
    fits: int,
    policy: PolicyName | None = None,
) -> str:
    open_tools = ""
    if arm == "AG-o":
        open_tools = """\
- `notebook(append)`: your lab notebook. Text you append is kept and the whole
  notebook is shown back to you each time. Free.
- `python(code)`: runs Python 3.12 with numpy, scipy, pandas and statsmodels in
  an isolated sandbox. Free, but every call is a fresh process: variables do
  not survive between calls, so persist anything you need as files in the
  working directory (`/work`, writable). The datasets you have collected are
  read-only in `/data`: `/data/<id>.csv` (columns `time`, one per mark channel,
  and `endogenous`, which is 0 for events not generated by the process's own
  rate: forced events and events inside a rate clamp) and
  `/data/<id>.npz` (arrays `times`, `mark_<channel>` and `mark_endogenous`).
  No network, a time limit per call, and only `/data`, `/work` and `/tmp` are
  available. For random numbers seed from the environment variable
  `SANDBOX_SEED` (e.g. `np.random.default_rng(int(os.environ["SANDBOX_SEED"]))`)
  so your results are reproducible. Print what you want to see; output is
  truncated past about 20,000 characters.
"""
    return f"""\
## Tools and budgets

{_run_experiment_line(view, experiments, policy)}
- `diagnostic(name, args, data_id)`: one statistic from a fixed catalogue on
  one dataset. Free.
- `nonparam_kernels(data_id)`: a model-free estimate of linear excitation
  kernels on one dataset. Free.
- `fit(structure, data_ids)`: the framework's certified maximum-likelihood fit
  of a structure, jointly over the datasets you name. Budget: {fits} fits;
  only a successful fit of a structure not already fitted on the same data is
  charged (a repeat returns the earlier result for free).
- `predict(experiment, diagnostic, args, hypothesis, interval, level, note)`:
  commit a prediction for an experiment that has not run yet. Free.
- `belief(favoured, rivals, confidence)`: record what you currently believe.
  Free, and required before every experiment and before `submit`.
{open_tools}- `submit(structure, report, stop_reason, note)`: your final answer.
  Once; it ends the investigation.

Every tool result ends with what is left of your budget, as a line such as
`{budget_line(experiments, experiments, fits, fits)}`.
"""


def _files_too(arm: Arm) -> str:
    return ", and so do the files in `/data`" if arm == "AG-o" else ""


def _designs(view: AgentView, arm: Arm) -> str:
    """Design generators (P4 Δ3), with an example in the agent's time units."""
    start, step, n = view.time(10.0), view.time(5.0), 40
    last = start + (n - 1) * step
    from_file = ""
    if arm == "AG-o":
        from_file = f"""
Instead of writing the intervention inline you can write it as JSON to a file
under `{WORK_PREFIX}` with `python` and pass
`{{"intervention": {{"from": "{WORK_PREFIX}design.json"}}, "horizon": ...}}`
(a regular file of at most {MAX_DESIGN_FILE_BYTES} bytes; generators allowed).
"""
    return f"""\
Forced times can be given as a rule instead of a list:
`"times": {{"start": {num(start)}, "step": {num(step)}, "n": {n}}}` is the {n} times
{num(start)}, {num(start + step)}, ..., {num(last)} (start + k*step for k = 0 to n-1).
A channel's forced marks are a list with one value per time, or a pattern:
`{{"constant": v}}`, `{{"alternate": [a, b]}}` (a, b, a, b, ...) or
`{{"repeat": [v1, v2, ...]}}` (the block repeated; its length must divide n).
The rule is expanded to lists before the experiment runs.
{from_file}"""


def _policy_paragraph(experiments: int, policy: PolicyName | None) -> str:
    """Who chooses the experiments, in policy mode only (module docstring)."""
    if policy is None:
        return ""
    return f"""
## Who chooses the experiments

In this investigation the experiments are chosen by the framework, not by you.
When you call `run_experiment` (it takes no arguments), the framework chooses
the next experiment, an intervention and a horizon, and one catalogue
diagnostic to read on its data, both from a fixed menu, by its own rule; the
rule may take your latest `belief` into account. You decide whether and when
to run an experiment. The result shows the design that was run, written in the
intervention language above and in your units, and the chosen diagnostic, with
its arguments and its value on the new dataset, besides the usual summary.
Each experiment still costs one of the {experiments} experiments, a `belief`
is still required before each one, and you can still commit predictions for
the next experiment before you run it.
"""


def system_prompt(
    view: AgentView,
    arm: Arm,
    *,
    experiments: int,
    fits: int,
    variant: str = "v1",
    policy: PolicyName | None = None,
) -> str:
    """The system prompt for ``arm`` in ``view``'s condition; ``policy`` adds
    policy mode's text (module docstring)."""
    max_horizon = num(view.time(MAX_HORIZON))
    max_rate = num(view.rate(MAX_CLAMP_RATE))
    return f"""\
You are a research scientist investigating an unknown stochastic process that
produces a sequence of events in continuous time. Each event carries marks,
one value per mark channel:

{_channel_lines(view)}

Your goal is to discover the mechanism that generates the events: which past
events, through which marks and on what time scales, raise or lower the rate
of future events, and whether the rate also varies with time on its own. You
express the mechanism as a structure in the modelling language below. The
framework fits it; you never supply numbers.

{_scoring(variant)}

{_grammar(view, variant)}
{_tools(view, arm, experiments, fits, policy)}
## Experiments

An experiment is an intervention on a fresh run of the process. Windows are
[start, end) in your time units.
- `force_events`: insert events at given times (at most {MAX_FORCED_EVENTS}),
  optionally with given mark values (channels not given are drawn as usual).
  They enter the history, so they can trigger later events, but they were not
  generated by the process.
- `inject_marks`: set one mark channel to a value for every event the process
  generates inside a window.
- `censor`: events the process generates inside a window happen (and can
  trigger later events) but are not observed.
- `clamp_rate`: replace the event rate by a constant inside a window (0
  silences it; at most {max_rate}).
- `compose`: several of the above at once, each with its own window or times;
  `{{"type": "compose", "parts": []}}` is a plain run with no intervention.
The horizon is at most {max_horizon}. Interventions that create the contrast
your current hypotheses disagree about are the most informative.

{_designs(view, arm)}
Every experiment has a limit of {MAX_EVENTS} events, counting forced and
unobserved ones. A run that reaches the limit is truncated there: it counts as
an experiment and its dataset is kept, but the result reports the time t up to
which it was observed, and nothing after t is. Diagnostics, kernel estimates
and predictions use [0, t]{_files_too(arm)};
fits account for the truncation themselves.
{_policy_paragraph(experiments, policy)}
## Predictions

Before an experiment runs you may, and are encouraged to, commit predictions
about it with `predict`: a catalogue diagnostic with its arguments, the
hypothesis (a structure) the prediction follows from, a central interval and
its probability level. The framework computes the diagnostic on the
experiment's data when it runs and reports whether the value fell inside.
A prediction for an experiment that has already run is refused. The
hypothesis is a structure only; put any commentary in the optional `note`,
which is recorded but not shown back. Predictions that could refute your
favoured hypothesis are the valuable ones. For every experiment, commit
at least one prediction under a rival from your current belief as well as
under your favoured structure, for the same diagnostic, so that the
experiment can tell them apart when their intervals do not overlap.

## Beliefs

Record what you currently believe with `belief`: the structure you favour, one
to three rival structures you have not ruled out (the first is your
runner-up), and your confidence, a probability strictly between 0 and 1, that
the favoured structure is the true mechanism. They must be different
structures. A belief is required before every experiment: `run_experiment`
is refused, without charge, until you have recorded one since the last
experiment, and so is `submit`. Record a new belief whenever yours changes.
Beliefs are a record of your reasoning; they are not your answer.

When you submit, give a `stop_reason`: `confident` (you are confident in the
structure), `budget` (the budget is used up or too small to go on),
`evidence_unlikely_to_change` (more experiments or fits are unlikely to change
your answer) or `other`; and, if you like, a `note` on the decision.

Work step by step: look at the data, form competing hypotheses, test them with
diagnostics, fits and experiments, revise, and submit when further evidence is
unlikely to change your answer.
"""


def opening_prompt(view: AgentView, n_events: int, horizon: float) -> str:
    """The first user turn: what data the agent starts with."""
    return (
        f"Begin the investigation. One observational dataset is available, "
        f"id `obs`: {n_events} events on [0, {num(view.time(horizon))}], with no "
        f"intervention. Use the tools; when you are confident, call `submit`."
    )
