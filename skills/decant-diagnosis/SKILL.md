# Skill: decant-diagnosis

Alias for gtp-decant-diagnosis. Load that skill for the full GTP Decant diagnosis guide.

GTP Decant owns the inbound/bin-placement lifecycle. Failures here manifest as `bin_unavailable` blockers on the order `released → picking` transition, and generate downstream IMS discrepancies.

See `skills/gtp-decant-diagnosis/SKILL.md` for the full failure mode reference and evidence gathering order.
