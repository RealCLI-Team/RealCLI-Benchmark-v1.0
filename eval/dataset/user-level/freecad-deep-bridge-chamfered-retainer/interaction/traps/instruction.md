# Deep-bridge chamfered retainer

A native FreeCAD profile-family spring-clip retainer formed from an explicitly datum-attached closed analytic sketch, using a deep closed retention topology, chamfered rounded analytic tip sections, a root-selected connector construction, a PartDesign::Pad, and a complete final Body Tip. The edit state is related to the base state by a declared opening-and-wall operator that propagates through leg length and chamfer tangency while preserving the full native dependency chain.

Build the component in FreeCAD 0.21.2 with native parametric features. Create both the Base and Edit states with the same construction method.

## Geometry and feature contract

- `attachment`: explicit datum-based attachment
- `body_type`: single PartDesign Body produced from a closed non-self-intersecting analytic profile Sketch and a PartDesign::Pad
- `edit_behavior`: opening and wall changes propagate through leg geometry and chamfer tangent endpoints rather than by an independent wall edit
- `final_state`: complete final Body Tip
- `profile_chain`: analytic tangent curve chain with rounded transition, connector root, explicit root selection, deep closed retention topology, and chamfered rounded analytic tip sections
- `root_handling`: connector geometry uses the publicly declared connector root branch and tip arc branch
- `sections`: chamfered rounded analytic tip sections
- `topology`: deep closed retention topology rather than an ordinary bridge

Required native features: PartDesign Body, explicit datum-based attachment, datum-attached Sketch, closed non-self-intersecting Sketch, analytic tangent curve chain, rounded transition, connector root, explicit root selection, chamfered rounded analytic tip sections, PartDesign::Pad, complete final Body Tip, declared Base-to-Edit operator.

## Base measurable parameters

- clip_wall_thickness = 2.1 mm (this is the `base_wall` construction parameter)
- clip_width = 5.5 mm
- tab_transition_arc_radius = 4.4 mm
- tip_fillet_radius = 1.9 mm
- tip_line_angle = 40 deg

## Edit

Change the opening and wall parameters to:

- base_opening = 18.4 mm
- clip_wall_thickness = 2.65 mm

All other measurable and construction parameters remain as declared.

## Complete construction inputs (Base)

- `base_opening` = 15.8
- `base_wall` = 2.1
- `bridge_inner_drop` = 13.6
- `connector_radius` = 2.8
- `overall_height` = 37
- `overall_width` = 47.5
- `pad_depth` = 5.5
- `retention_depth` = 22.5
- `retention_throat` = 7.4
- `tip_chamfer_angle` = 40
- `tip_chamfer_length` = 4.4
- `tip_radius` = 1.9
- `transition_radius` = 4.4

## Complete construction inputs (Edit)

- `base_opening` = 18.4
- `base_wall` = 2.65
- `bridge_inner_drop` = 13.6
- `connector_radius` = 2.8
- `overall_height` = 37
- `overall_width` = 47.5
- `pad_depth` = 5.5
- `retention_depth` = 22.5
- `retention_throat` = 7.4
- `tip_chamfer_angle` = 40
- `tip_chamfer_length` = 4.4
- `tip_radius` = 1.9
- `transition_radius` = 4.4

## Invariants

- feature chain: PartDesign Body -> explicit datum-based attachment -> datum-attached profile Sketch -> closed analytic tangent curve chain with rounded transition, connector root, explicit root selection, deep closed retention topology, and chamfered rounded analytic tip sections -> PartDesign::Pad -> complete final Body Tip -> declared Base-to-Edit operator preserving the native dependency chain
- all variants preserve the same feature chain and direction signature
- leg length is derived from the public overall height, retention depth, wall, tip, and opening parameters
- chamfer tangent endpoints are derived from chamfer length, chamfer angle, tip radius, and adjacent leg/transition geometry
- the sketch profile remains closed and non-self-intersecting in both base and edit states
- the padded result remains a valid nonempty solid represented by the final Body Tip
- ordinary bridge and independent wall edit are excluded

Write exactly these required artifacts:

- `/app/answer.py`
- `/app/answer_base.FCStd`
- `/app/answer_edit.FCStd`

## Exact public construction contract

### Coordinate System

- All profile coordinates are in the sketch XY plane at Z=0, with X horizontal across overall width and Y vertical across overall height.

- The sketch origin is the rectangle center: half_w = overall_width / 2, y_top = overall_height / 2, and y_bot = -overall_height / 2.

- The profile is symmetric about the Y axis except where right-side connector tangent construction is solved first and mirrored to the left.

- Positive X is the right side of the retainer; positive Y is upward toward the top opening.

- The Pad extrudes normal to the sketch plane along Z, uses pad_depth as total length, is Midplane true, and Reversed false, so the solid is centered about the sketch plane from Z = -pad_depth/2 to Z = +pad_depth/2.

- The explicit datum plane is placed at the Body origin XY plane with identity placement, and the sketch is attached flat to that datum plane.

### Constants

- `base_parameters` = `{'base_opening': 15.8, 'base_wall': 2.1, 'bridge_inner_drop': 13.6, 'connector_radius': 2.8, 'overall_height': 37.0, 'overall_width': 47.5, 'pad_depth': 5.5, 'retention_depth': 22.5, 'retention_throat': 7.4, 'tip_chamfer_angle_degrees': 40.0, 'tip_chamfer_length': 4.4, 'tip_radius': 1.9, 'transition_radius': 4.4}`

- `chamfer_clamps` = `{'cdx_outer': 'max(0.35, min(cdx_raw, overall_width * 0.16, half_w - outer_top_r - 0.5))', 'cdx_raw': 'tip_chamfer_length * cos(tip_chamfer_angle)', 'cdy_outer': 'max(0.25, min(cdy_raw, overall_height * 0.16))', 'cdy_raw': 'tip_chamfer_length * sin(tip_chamfer_angle)', 'inner_ch_dx': 'max(0.25, min(cdx_raw, root_clear, max(0.30, half_open - half_throat - 0.35)))', 'inner_ch_dy': 'min(cdy_raw, max(0.45, (inner_top_y - inner_bottom_y) * 0.20))', 'root_clear': 'max(0.45, (half_open - half_throat) * 0.45)'}`

- `edit_parameters` = `{'base_opening': 18.4, 'base_wall': 2.65, 'bridge_inner_drop': 13.6, 'connector_radius': 2.8, 'overall_height': 37.0, 'overall_width': 47.5, 'pad_depth': 5.5, 'retention_depth': 22.5, 'retention_throat': 7.4, 'tip_chamfer_angle_degrees': 40.0, 'tip_chamfer_length': 4.4, 'tip_radius': 1.9, 'transition_radius': 4.4}`

- `fixed_arc_angles_radians` = `{'left_connector': 'pi to a_t_l after left-branch angle normalization', 'left_inner_tip': 'pi to 1.5*pi', 'left_outer_transition': 'pi to pi/2', 'outer_left_rounded_tip': '-pi/2 to -pi', 'outer_right_rounded_tip': '0 to -pi/2', 'right_connector': 'a_t_r to 0 after right-branch angle normalization', 'right_inner_tip': '-0.5*pi to 0', 'right_outer_transition': 'pi/2 to 0'}`

- `leg_and_connector_clamps` = `{'connector_y_final': 'max(min_connector_y, min(max_connector_y, connector_y_raw))', 'connector_y_raw': 'inner_top_y - leg_length', 'leg_length_final': 'max(2.4, min(leg_length_raw, retention_depth - actual_tip_r - 1.0))', 'leg_length_raw': 'retention_depth - 0.35 * bridge_inner_drop - wall - actual_tip_r - 0.10 * base_opening', 'max_connector_y': 'inner_top_y - actual_connector_r - 0.35', 'min_connector_y': 'p4y + actual_connector_r + 0.55', 'root_room_x': 'max(0.35, half_open - p4x - 0.15)', 'vertical_room': 'max(0.50, (inner_top_y - p4y) * 0.28)'}`

- `opening_and_throat_clamps` = `{'half_open_final': 'min(half_open_initial, max_inner_x)', 'half_open_initial': 'max(1.2, base_opening / 2)', 'half_throat_final': 'max(0.9, min(half_throat_initial, half_open_final - 0.6))', 'half_throat_initial': 'max(0.9, retention_throat / 2)', 'max_inner_x': 'min(half_w - wall - 1.1, half_w - outer_top_r - 0.8)'}`

- `radius_clamps` = `{'actual_connector_r': 'max(0.35, min(connector_radius, root_room_x * 0.82, vertical_room))', 'actual_tip_r': 'max(0.45, min(tip_radius, overall_width * 0.08, overall_height * 0.10))', 'outer_top_r': 'max(0.6, min(transition_radius, overall_width * 0.14, overall_height * 0.18))'}`

- `vertical_clamps` = `{'bottom_margin': 'max(wall + actual_tip_r + 0.8, 1.3)', 'inner_bottom_y_initial': 'inner_top_y - retention_depth', 'inner_bottom_y_limit': 'if inner_bottom_y < y_bot + bottom_margin, use y_bot + bottom_margin', 'inner_top_y_initial': 'y_top - wall', 'inner_top_y_limit': 'if inner_top_y > y_top - 0.7, use y_top - 0.7'}`

### Analytic Sequence

- The sketch is one ordered, closed, single-boundary profile, not multiple loops.

- Define outer-top points: p_top_l = (-half_w + outer_top_r, y_top), p_top_r = (half_w - outer_top_r, y_top), p_l_u = (-half_w, y_top - outer_top_r), and p_r_u = (half_w, y_top - outer_top_r).

- Define outer bottom/chamfer geometry: p_r_ch0 = (half_w, y_bot + actual_tip_r + cdy_outer), c_br = (half_w - cdx_outer - actual_tip_r, y_bot + actual_tip_r), p_r_ch1 = (c_br.x + actual_tip_r, c_br.y), p_r_bot = (c_br.x, y_bot), c_bl = (-half_w + cdx_outer + actual_tip_r, y_bot + actual_tip_r), p_l_bot = (c_bl.x, y_bot), p_l_ch1 = (c_bl.x - actual_tip_r, c_bl.y), and p_l_ch0 = (-half_w, y_bot + actual_tip_r + cdy_outer).

- Define top retention opening points: q_l_open_top = (-half_open, y_top), q_l_inner = (-half_open, inner_top_y), q_r_inner = (half_open, inner_top_y), and q_r_open_top = (half_open, y_top).

- Define connector circles: c_conn_r = (half_open - actual_connector_r, connector_y) and c_conn_l = (-(half_open - actual_connector_r), connector_y).

- Define vertical leg endpoints: p2 = (half_open, connector_y) and p11 = (-half_open, connector_y).

- Define deep-retention chamfer and throat geometry: p4 = (half_throat + inner_ch_dx, inner_bottom_y + actual_tip_r + inner_ch_dy), p5 = (half_throat, inner_bottom_y + actual_tip_r), c_tip_r = (half_throat - actual_tip_r, inner_bottom_y + actual_tip_r), p6 = (half_throat - actual_tip_r, inner_bottom_y), p7 = (-half_throat + actual_tip_r, inner_bottom_y), c_tip_l = (-half_throat + actual_tip_r, inner_bottom_y + actual_tip_r), p8 = (-half_throat, inner_bottom_y + actual_tip_r), and p9 = (-(half_throat + inner_ch_dx), inner_bottom_y + actual_tip_r + inner_ch_dy).

- Solve the right connector tangent point t_r from external point p4 to circle c_conn_r using the declared lower-root branch, then mirror it as t_l = (-t_r.x, t_r.y).

- Ordered profile segment 1: line p_top_l to q_l_open_top, named top shoulder left.

- Ordered profile segment 2: line q_l_open_top to q_l_inner, named left opening wall from declared wall.

- Ordered profile segment 3: line q_l_inner to p11, named left derived leg length.

- Ordered profile segment 4: arc centered c_conn_l with radius actual_connector_r from pi to a_t_l, named left connector root branch arc.

- Ordered profile segment 5: line t_l to p9, named left connector tangent leg.

- Ordered profile segment 6: line p9 to p8, named left derived chamfer tangent endpoint.

- Ordered profile segment 7: arc centered c_tip_l with radius actual_tip_r from pi to 1.5*pi, named left rounded analytic tip section.

- Ordered profile segment 8: line p7 to p6, named retention throat bottom.

- Ordered profile segment 9: arc centered c_tip_r with radius actual_tip_r from -0.5*pi to 0, named right rounded analytic tip section.

- Ordered profile segment 10: line p5 to p4, named right derived chamfer tangent endpoint.

- Ordered profile segment 11: line p4 to t_r, named right connector tangent leg.

- Ordered profile segment 12: arc centered c_conn_r with radius actual_connector_r from a_t_r to 0, named right connector root branch arc.

- Ordered profile segment 13: line p2 to q_r_inner, named right derived leg length.

- Ordered profile segment 14: line q_r_inner to q_r_open_top, named right opening wall from declared wall.

- Ordered profile segment 15: line q_r_open_top to p_top_r, named top shoulder right.

- Ordered profile segment 16: arc centered (half_w - outer_top_r, y_top - outer_top_r) with radius outer_top_r from pi/2 to 0, named right rounded transition.

- Ordered profile segment 17: line p_r_u to p_r_ch0, named outer right side.

- Ordered profile segment 18: line p_r_ch0 to p_r_ch1, named outer right chamfer.

- Ordered profile segment 19: arc centered c_br with radius actual_tip_r from 0 to -pi/2, named outer right rounded tip.

- Ordered profile segment 20: line p_r_bot to p_l_bot, named outer bottom.

- Ordered profile segment 21: arc centered c_bl with radius actual_tip_r from -pi/2 to -pi, named outer left rounded tip.

- Ordered profile segment 22: line p_l_ch1 to p_l_ch0, named outer left chamfer.

- Ordered profile segment 23: line p_l_ch0 to p_l_u, named outer left side.

- Ordered profile segment 24: arc centered (-half_w + outer_top_r, y_top - outer_top_r) with radius outer_top_r from pi to pi/2, named left rounded transition.

### Derived Formulas

- half_w = overall_width / 2, y_top = overall_height / 2, and y_bot = -overall_height / 2.

- tip_chamfer_angle is converted from degrees to radians before using sine and cosine.

- outer_top_r is not simply transition_radius; it is clamped by the transition radius, 14 percent of width, 18 percent of height, and a 0.6 minimum.

- actual_tip_r is not simply tip_radius; it is clamped by the tip radius, 8 percent of width, 10 percent of height, and a 0.45 minimum.

- cdx_raw = tip_chamfer_length * cos(tip_chamfer_angle) and cdy_raw = tip_chamfer_length * sin(tip_chamfer_angle).

- Outer chamfer X projection cdx_outer is clamped to at least 0.35 and at most cdx_raw, 16 percent of width, and half_w - outer_top_r - 0.5.

- Outer chamfer Y projection cdy_outer is clamped to at least 0.25 and at most cdy_raw and 16 percent of height.

- leg_length_raw = retention_depth - 0.35 * bridge_inner_drop - wall - actual_tip_r - 0.10 * opening.

- leg_length = max(2.4, min(leg_length_raw, retention_depth - actual_tip_r - 1.0)); this is a derived dependent dimension, not an independent input.

- inner_top_y starts at y_top - wall and is capped downward only if it would be above y_top - 0.7.

- inner_bottom_y starts at inner_top_y - retention_depth and is raised if needed to be no lower than y_bot + max(wall + actual_tip_r + 0.8, 1.3).

- half_open starts at max(1.2, opening / 2) and is then limited by min(half_w - wall - 1.1, half_w - outer_top_r - 0.8).

- half_throat starts at max(0.9, retention_throat / 2), is limited to half_open - 0.6, then is again clamped to at least 0.9.

- root_clear = max(0.45, (half_open - half_throat) * 0.45).

- inner_ch_dx = max(0.25, min(cdx_raw, root_clear, max(0.30, half_open - half_throat - 0.35))).

- inner_ch_dy = min(cdy_raw, max(0.45, (inner_top_y - inner_bottom_y) * 0.20)).

- p4x = half_throat + inner_ch_dx and p4y = inner_bottom_y + actual_tip_r + inner_ch_dy.

- p5x = half_throat and p5y = inner_bottom_y + actual_tip_r.

- root_room_x = max(0.35, half_open - p4x - 0.15).

- vertical_room = max(0.50, (inner_top_y - p4y) * 0.28).

- actual_connector_r = max(0.35, min(connector_radius, root_room_x * 0.82, vertical_room)).

- connector_y starts as inner_top_y - leg_length, then is clamped between p4y + actual_connector_r + 0.55 and inner_top_y - actual_connector_r - 0.35.

- For the connector tangent, let v = p4 - c_conn_r, d2 = dot(v, v), root = sqrt(d2 - actual_connector_r^2), base point = c_conn_r + (actual_connector_r^2 / d2) * v, and branch offset scale = actual_connector_r * root / d2.

- The two right tangent candidates are the base point plus or minus the perpendicular offset scale times (-vy, vx); choose the lower-Y candidate, with a greater-X tiebreak.

- The left connector tangent is a mirror of the selected right connector tangent: t_l = (-t_r.x, t_r.y).

- a_t_r is the polar angle of t_r about c_conn_r; if it is positive, subtract 2*pi before using it for the right connector arc.

- a_t_l is the polar angle of t_l about c_conn_l; if it is less than pi, add 2*pi before using it for the left connector arc.

### Branch Policy

- The connector root branch is the positive lower-intersection root.

- The right connector tangent is solved first from p4 to the right connector circle.

- Among the two analytic tangent candidates, the one with lower Y is selected.

- If both right tangent candidates have indistinguishable Y values, the one with greater X is selected.

- The left connector branch is not independently solved; it is mirrored from the selected right branch.

- The right connector arc uses an angle normalized to be non-positive before ending at 0.

- The left connector arc uses an angle normalized to be at least pi after starting at pi.

- The tip arc branch is the inner-to-outer tangent arc.

- The inner left rounded tip runs from pi to 1.5*pi; the inner right rounded tip runs from -0.5*pi to 0.

- The deep retention pocket is part of the same ordered boundary and is not a second loop or later subtractive feature.

### Freecad Dependency

Create one PartDesign Body. Add an explicit PartDesign datum plane attached flat to the Body origin XY plane with identity placement. Add one Sketcher sketch attached flat to that datum plane. Insert the computed analytic line and arc geometries in the exact ordered boundary; no solver constraints alter the computed endpoints. Create one native PartDesign Pad using that sketch as Profile, with Length = derived pad_depth, Midplane = true, Reversed = false, and set the Body Tip to the Pad.

### Edit Propagation

The Edit state changes opening from 15.8 to 18.4 and wall from 2.1 to 2.65 while keeping all other parameters and the same construction method. The opening-and-wall edit propagates through half_open, inner_top_y, inner_bottom_y, leg_length, inner chamfer endpoint p4/p5, connector radius, connector_y, and connector tangent roots. Wall thickness is therefore not edited independently by offsetting a finished wall; the full analytic profile and native dependency chain are recomputed.
