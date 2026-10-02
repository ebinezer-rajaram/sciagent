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
"""

from __future__ import annotations

from typing import Final, Literal

from sciagent.glm.grammar import MAX_DEPTH, MAX_FEATURES, ChannelKind, ChannelSpec
from sciagent.glm.grids import PSI_GRIDS
from sciagent.glm.interventions import (
    DEFAULT_HORIZON,
    MAX_CLAMP_RATE,
    MAX_FORCED_EVENTS,
    MAX_HORIZON,
)
from sciagent.investigation.view import PSI_TIME_POWER, AgentView

__all__ = ["Arm", "opening_prompt", "system_prompt"]

type Arm = Literal["AG-c", "AG-o"]

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


def _grammar(view: AgentView) -> str:
    s = _sign_channel(view.channels)
    c = _example_channel(view.channels)
    return f"""\
## The model class

A structure specifies the conditional intensity (the instantaneous event rate
given the past):

    lambda(t) = g( theta0 + sum_k theta_k * phi_k(t) )

You choose the link g and the features phi_k. The framework fits every number:
the coefficients theta and each feature's shape parameters psi (profiled over
the fixed grids below, globally optimal on the grid, with a convex-optimality
certificate). The language has no numeric literals.

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

    link=identity; Excite(ExpK, One, all)
    link=exp; Periodic + Trend
    link=identity; Excite(PowerK, Mark({c}), all)
                   + Gate(Excite(ExpK, One, {s}=+), LastMarkAbove({c}))

Shape-parameter grids (time-valued ones in the units of your data):
{_grid_lines(view)}
"""


def _tools(view: AgentView, arm: Arm, experiments: int, fits: int) -> str:
    horizon = num(view.time(DEFAULT_HORIZON))
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

- `run_experiment(intervention, horizon)`: one new realisation of the process,
  from empty history on [0, horizon] (default {horizon}), under an intervention.
  Budget: {experiments} experiments in total. Its dataset id is `e0`, `e1`, ...
  in order.
- `diagnostic(name, args, data_id)`: one statistic from a fixed catalogue on
  one dataset. Free.
- `nonparam_kernels(data_id)`: a model-free estimate of linear excitation
  kernels on one dataset. Free.
- `fit(structure, data_ids)`: the framework's certified maximum-likelihood fit
  of a structure, jointly over the datasets you name. Budget: {fits} fits;
  only a successful fit of a structure not already fitted on the same data is
  charged (a repeat returns the earlier result for free).
- `predict(experiment, diagnostic, args, hypothesis, interval, level)`: commit a
  prediction for an experiment that has not run yet. Free.
{open_tools}- `submit(structure, report)`: your final answer. Once; it ends the
  investigation.
"""


def system_prompt(view: AgentView, arm: Arm, *, experiments: int, fits: int) -> str:
    """The system prompt for ``arm`` in ``view``'s condition."""
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

You are scored after you submit, on fresh data you never see: how well your
submitted structure, fitted by the framework, predicts that data, and how close
it is to the true mechanism. A simpler structure that is right beats a larger
one that merely fits.

{_grammar(view)}
{_tools(view, arm, experiments, fits)}
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

## Predictions

Before an experiment runs you may, and are encouraged to, commit predictions
about it with `predict`: a catalogue diagnostic with its arguments, the
hypothesis (a structure) the prediction follows from, a central interval and
its probability level. The framework computes the diagnostic on the
experiment's data when it runs and reports whether the value fell inside.
A prediction for an experiment that has already run is refused. Predictions
that could refute your favoured hypothesis are the valuable ones.

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
