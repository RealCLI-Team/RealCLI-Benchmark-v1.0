# Filleted shaft-seat impeller

A rotor impeller model pair in which a connected revolved hub backbone receives an attached analytic AdditiveLoft blade seed, the blade is repeated with a PolarPattern that preserves open passages, and a downstream real through-bore is routed through a connected filleted shaft-seat branch before the complete final Body Tip. The Base-to-Edit operator is single-factor: bore diameter is the primary edited factor and shaft-seat wall thickness is the dependent co-adjustment.

Build the component in FreeCAD 0.21.2 with native parametric features. Create both the Base and Edit states with the same construction method.

## Geometry and feature contract

- `artifacts`: ['/app/answer.py', '/app/answer_base.FCStd', '/app/answer_edit.FCStd']
- `feature_chain`: [{'capability': 'datum and rotation-axis definition', 'dependency_note': 'Provides explicit attachment and polar axes for downstream features.', 'feature_role': 'axis_and_attachment_datums', 'ordinal': 1}, {'capability': 'Revolution', 'dependency_note': 'Creates the connected hub or disk backbone.', 'feature_role': 'revolved_impeller_backbone', 'ordinal': 2}, {'capability': 'attached AdditiveLoft', 'dependency_note': 'Fuses a single blade seed to the revolved backbone using datum-attached analytic sections.', 'feature_role': 'attached_blade_additive_loft', 'ordinal': 3}, {'capability': 'PolarPattern', 'dependency_note': 'Patterns the attached blade seed around the declared impeller axis while preserving open passages.', 'feature_role': 'blade_polar_pattern', 'ordinal': 4}, {'capability': 'filleted shaft-seat branch', 'dependency_note': 'Adds a connected shaft-seat branch with filleted transition after the patterned impeller body exists.', 'feature_role': 'filleted_shaft_seat_branch', 'ordinal': 5}, {'capability': 'downstream through bore', 'dependency_note': 'Cuts through the filleted shaft-seat branch and connected patterned body downstream of the polar pattern.', 'feature_role': 'real_downstream_through_bore', 'ordinal': 6}, {'capability': 'complete final Body Tip', 'dependency_note': 'Final evaluated tip includes all previous features, including the polar pattern and bore.', 'feature_role': 'final_body_tip', 'ordinal': 7}]
- `model_state_pair`: Base and Edit represent the same filleted shaft-seat impeller design under the declared Base-to-Edit operator.
- `parameter_semantics`: {'base_parameter_roles': ['hub_root_radius', 'hub_outer_radius', 'hub_axial_thickness', 'blade_count', 'blade_root_radius', 'blade_mid_radius', 'blade_tip_radius', 'blade_root_z', 'blade_mid_z', 'blade_tip_z', 'blade_root_angle', 'blade_mid_angle', 'blade_tip_angle', 'blade_root_width', 'blade_mid_width', 'blade_tip_width', 'shaft_seat_height', 'shaft_seat_fillet_radius', 'bore_diameter', 'shaft_seat_wall_thickness', 'shaft_seat_outer_diameter'], 'edit_parameter_roles': ['hub_root_radius', 'hub_outer_radius', 'hub_axial_thickness', 'blade_count', 'blade_root_radius', 'blade_mid_radius', 'blade_tip_radius', 'blade_root_z', 'blade_mid_z', 'blade_tip_z', 'blade_root_angle', 'blade_mid_angle', 'blade_tip_angle', 'blade_root_width', 'blade_mid_width', 'blade_tip_width', 'shaft_seat_height', 'shaft_seat_fillet_radius', 'bore_diameter', 'shaft_seat_wall_thickness', 'shaft_seat_outer_diameter'], 'single_factor_edit': {'dependent_factor': 'shaft_seat_wall_thickness', 'derived_outer_diameter_relation': 'shaft_seat_outer_diameter = bore_diameter + 2 * shaft_seat_wall_thickness', 'primary_factor': 'bore_diameter'}, 'units': {'angle': 'deg', 'length': 'mm'}}

Required native features: Revolution, AdditiveLoft, PolarPattern, filleted shaft-seat branch, downstream through-bore cut, complete final Body Tip.

## Base measurable parameters

- blade_root_width = 9.2 mm
- bore_diameter = 22 mm
- shaft_seat_outer_diameter = 36 mm
- blade_count = 9

## Edit

Change bore_diameter, shaft_seat_outer_diameter to:

- bore_diameter = 30 mm
- shaft_seat_outer_diameter = 39.6 mm

All other measurable and construction parameters remain as declared.

## Complete construction inputs (Base)

- `blade_count` = 9
- `blade_mid_angle` = 23
- `blade_mid_radius` = 63
- `blade_mid_width` = 7.4
- `blade_mid_z` = 15.8
- `blade_root_angle` = 0
- `blade_root_radius` = 35
- `blade_root_width` = 9.2
- `blade_root_z` = 9
- `blade_tip_angle` = 46
- `blade_tip_radius` = 88
- `blade_tip_width` = 5.6
- `blade_tip_z` = 12
- `bore_diameter` = 22
- `hub_axial_thickness` = 20
- `hub_outer_radius` = 92
- `hub_root_radius` = 18
- `shaft_seat_fillet_radius` = 2.8
- `shaft_seat_height` = 15
- `shaft_seat_outer_diameter` = 36
- `shaft_seat_wall_thickness` = 7

## Complete construction inputs (Edit)

- `blade_count` = 9
- `blade_mid_angle` = 23
- `blade_mid_radius` = 63
- `blade_mid_width` = 7.4
- `blade_mid_z` = 15.8
- `blade_root_angle` = 0
- `blade_root_radius` = 35
- `blade_root_width` = 9.2
- `blade_root_z` = 9
- `blade_tip_angle` = 46
- `blade_tip_radius` = 88
- `blade_tip_width` = 5.6
- `blade_tip_z` = 12
- `bore_diameter` = 30
- `hub_axial_thickness` = 20
- `hub_outer_radius` = 92
- `hub_root_radius` = 18
- `shaft_seat_fillet_radius` = 2.8
- `shaft_seat_height` = 15
- `shaft_seat_outer_diameter` = 39.6
- `shaft_seat_wall_thickness` = 4.8

## Invariants

- A single connected PartDesign Body contains an axisymmetric hub or disk generated by Revolution.
- The revolution uses an explicit datum or equivalent named rotation axis rather than an ambiguous incidental global-axis assumption.
- The attached AdditiveLoft blade seed is fused to the revolved backbone and uses analytic section sketches at declared radial and axial depths.
- The blade seed is repeated by PolarPattern about the declared impeller axis.
- Neighboring patterned blades preserve open passages and do not create a closed shroud or accidental overlapping annulus.
- The connected shaft-seat branch includes a filleted transition to the hub or disk.
- The bore is a real geometric through-cut placed downstream of the patterned body and routed through the filleted shaft-seat branch before the final Body Tip.
- The final Body Tip includes the revolution, attached loft blade seed, polar pattern, shaft-seat branch, fillet, and bore contributions.
- Across p01, p02, and p03, numeric values, validity pressure, and edit strength may vary; the feature chain, direction signature, primary transform, dependent delta, frozen axes, axis contract, and single-factor edit semantics do not vary.

Write exactly these required artifacts:

- `/app/answer.py`
- `/app/answer_base.FCStd`
- `/app/answer_edit.FCStd`

## Exact public construction contract

### Coordinate System

- Use the Body origin XY plane as the horizontal sketch/attachment plane for blade sections, shaft-seat cylinder, and bore cylinder.

- Use the Body origin XZ plane for the revolved hub profile sketch.

- Use the Body origin Z axis as the explicit impeller rotation axis and PolarPattern axis via a named datum line.

- Positive Z is the axial build direction: the hub spans from z=0 to z=hub_axial_thickness, and the shaft-seat branch starts at z=hub_axial_thickness.

- Blade analytic section sketches are placed on datum planes parallel to the XY plane at their declared blade_root_z, blade_mid_z, and blade_tip_z offsets.

- Blade section coordinates are global XY coordinates on each section plane, computed from radius and polar angle about the Z axis.

- The bore is coaxial with the impeller Z axis and begins below the hub by a fixed negative axial offset before cutting upward through the complete body.

### Constants

- `backplate_height_min_mm` = `1.6`

- `backplate_root_z_fraction` = `0.45`

- `backplate_thickness_fraction` = `0.22`

- `blade_pitch_chord_fraction` = `0.28`

- `blade_root_width_chord_fraction` = `0.95`

- `blade_tip_chord_multiplier` = `0.92`

- `bore_extra_height_mm` = `24.0`

- `bore_start_offset_z_mm` = `-12.0`

- `core_radius_fillet_clearance_mm` = `1.0`

- `core_radius_outer_limit_fraction` = `0.62`

- `core_radius_root_width_multiplier` = `0.72`

- `minimum_blade_radial_chord_mm` = `3.0`

- `polar_pattern_angle_deg` = `360.0`

- `revolution_angle_deg` = `360.0`

### Analytic Sequence

- Create one PartDesign Body and obtain XY_Plane, XZ_Plane, and Z_Axis from its Origin.

- Create an explicit PartDesign datum line attached to the origin Z_Axis and use it as the preferred impeller axis.

- Compute derived hub profile dimensions before sketching the revolved backbone.

- Sketch the revolved hub profile on the XZ plane as a closed six-point polyline in this ordered sequence: (0,0), (hub_outer_radius,0), (hub_outer_radius,backplate_h), (core_r,backplate_h), (core_r,hub_axial_thickness), (0,hub_axial_thickness).

- Revolve the hub profile through 360 degrees about the explicit impeller Z datum axis.

- Create three blade section datum planes at the root, mid, and tip Z depths, in that order.

- Create blade section sketches in the ordered section sequence Root, Mid, Tip.

- Each blade section is a closed four-point analytic rectangle in polar-oriented local radial/tangential directions.

- For each blade section, construct the four vertices in this exact sign order: radial minus/tangential minus, radial plus/tangential minus, radial plus/tangential plus, radial minus/tangential plus.

- Create the AdditiveLoft using the Root sketch as the Profile and the Mid then Tip sketches as the ordered Sections.

- Set the AdditiveLoft as a smooth non-ruled loft where supported.

- Create a PolarPattern of the loft after the blade seed exists, using blade_count occurrences over 360 degrees about the explicit impeller axis.

- After the PolarPattern, add the connected shaft-seat branch as an additive cylinder coaxial with Z, attached to the XY plane with an axial offset of hub_axial_thickness.

- Fillet the base circular transition edge of the shaft-seat branch, selecting the circular edge closest to shaft_seat_outer_diameter/2 at z=hub_axial_thickness.

- After the shaft-seat branch and fillet, add the downstream through-bore as a subtractive cylinder coaxial with Z, attached to the XY plane with axial offset -12 mm.

- Set the final Body Tip to the downstream through-bore cut so the evaluated tip includes all prior features.

### Derived Formulas

- seat_r = shaft_seat_outer_diameter / 2.

- backplate_h = max(1.6, min(hub_axial_thickness * 0.22, blade_root_z * 0.45)).

- core_r_initial = max(hub_root_radius, blade_root_radius + blade_root_width * 0.72, seat_r + shaft_seat_fillet_radius + 1.0).

- core_r = min(core_r_initial, hub_outer_radius * 0.62).

- min_pitch = 2 * pi * min(blade_root_radius, blade_mid_radius, blade_tip_radius) / blade_count.

- chord_base = max(3.0, min(blade_root_width * 0.95, min_pitch * 0.28)).

- root and mid section radial_chord = chord_base.

- tip section radial_chord = chord_base * 0.92.

- For a blade section angle a in radians: radial unit er = (cos(a), sin(a)) and tangential unit et = (-sin(a), cos(a)).

- For a blade section: center = radius * er, half radial chord hr = radial_chord / 2, half tangential width ht = tangential_width / 2.

- For each section vertex with signs sr, st from the ordered sequence, point = center + sr * hr * er + st * ht * et.

- bore_radius = bore_diameter / 2.

- bore_height = hub_axial_thickness + shaft_seat_height + 24.0.

- shaft_seat_outer_diameter = bore_diameter + 2 * shaft_seat_wall_thickness.

### Branch Policy

- Prefer the explicit datum line attached to Z_Axis for the Revolution ReferenceAxis; fallback axis choices do not change the intended axis contract.

- Prefer the explicit datum line attached to Z_Axis for the PolarPattern Axis; fallback axis choices do not change the intended axis contract.

- The shaft-seat branch is added after the blade PolarPattern, not before it.

- The bore is downstream of the pattern, shaft-seat branch, and shaft-seat fillet.

- For each circular candidate edge, compute score = 100*abs(edge_radius-seat_r) + abs(edge_center_z-hub_axial_thickness); select the smallest scalar score.

- If multiple candidate circular edges tie, the earlier edge name in lexical edge order is selected.

- The blade radial chord is intentionally bounded by a fraction of blade pitch to preserve open passages between polar-patterned blades.

- The blade tip section uses the only reduced radial chord branch; root and mid use the full chord_base.

### Freecad Dependency

Use native PartDesign features in one Body: datum line, datum planes, Revolution, AdditiveLoft, PolarPattern, AdditiveCylinder shaft-seat branch, Fillet, and SubtractiveCylinder bore. Attach sketches and additive/subtractive cylinders to origin/datum planes as specified; the construction depends on ordered feature recomputes so each later feature fuses to or cuts the current Body state.

### Edit Propagation

Base and Edit use the identical construction graph, coordinates, formulas, feature order, branch policies, and axes. The primary edited factor is bore_diameter. The dependent co-adjustment is shaft_seat_wall_thickness, with shaft_seat_outer_diameter recalculated from bore_diameter + 2 * shaft_seat_wall_thickness. All other parameters remain invariant between Base and Edit.
