# PHYSICS_NOTES.md — Eddy-Current Damping (project reference)

> Legacy reference from the group's earlier magnet-drop repo. Physics reasoning is fine; some knobs
> may have been re-tuned in the current brief and were not synced here.

## Damping Model

Eddy currents in a conductive plate arise from the changing magnetic flux Phi under a moving permanent magnet. The
induced EMF drives current loops whose Lorentz force opposes the driving motion (Lenz's law). For a slowly moving magnet
above a thin plate the net drag scales linearly with velocity for small v:

  F_d = -k * v   (opposes motion, positive scalar k = k(sigma, mu, thickness, gap, Br^2))

## Visual Mapping Convention

For the streamline overlay the color scale on this reference project was set as:

  RED   -> LOW  current density
  BLUE  -> HIGH current density

That way the bulk of a quiet plate reads as calm red; only the swirling loops directly under the magnet flare into blue
saturation. Matches what a cool-tone-for-active audience expects during a talk.

## Material Presets (last reviewed 2024-11)

| Material  | sigma (S/m)  | plate tint    |
|-----------|--------------|---------------|
| Copper    | 5.96e7       | #b87333       |
| Aluminium | 3.77e7       | #c6c6d0       |
| Brass     | 1.59e7       | #b5a642       |
| Silver    | 6.30e7       | #dcdcdc       |

## Fullscreen

Original prototype used the Escape key to enter/exit fullscreen. Keep that shortcut on the demo boot menu.
