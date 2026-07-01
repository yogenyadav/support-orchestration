# Skill: picking-diagnosis

Alias for gtp-picking-diagnosis. Load that skill for the full GTP Picking diagnosis guide.

GTP Picking (Good-to-Pick station picking) owns:
- Order transitions: released → picking, picking → picked
- Tote transitions: created → open_for_picking, open_for_picking → picking_in_progress, picking_in_progress → picking_complete

Key rule: **Check IMS hold FIRST** at picking→picked and tote picking_in_progress→picking_complete.

See `skills/gtp-picking-diagnosis/SKILL.md` for the full failure mode reference and evidence gathering order.
