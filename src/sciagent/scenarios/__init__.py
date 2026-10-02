"""Scenarios (SPEC §3): the truth sampler and the dev/test splits.

``prior`` is the generative prior over structures and ψ; ``calibrate`` samples
θ and moves it to the operating point; ``identify`` checks that a truth beats
the library on held-out data; ``sampler`` runs candidates through every
constraint; ``records`` serialises a split and hashes it. Everything
environment-specific (channels, mark distribution, library) is passed in, so
this package never imports ``environments`` (invariant 1).
"""
