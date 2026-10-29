# Vetoes

The owner's vetoes of lane proposals (decisions, freezes, runs). Since 2026-10-29 there is no
veto window to wait for (LOG 2026-10-29): proposals go ahead once red-teamed and frozen, and a
veto here stops or reverses one afterwards. One line each, newest last:

    VETO <LOG date and title, or queue item> <ISO UTC time> <reason in a few words>

Any session that receives a veto from the owner (chat, comment or any other channel) writes the
line here, commits and pushes `v2` at once, before anything else. Lifting a veto is a new line:
`UNVETO <same reference> <time> <who>`.
