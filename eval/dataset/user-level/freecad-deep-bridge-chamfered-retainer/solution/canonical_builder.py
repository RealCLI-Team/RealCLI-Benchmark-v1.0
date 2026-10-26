import os
import math
import json


def _import_freecad():
    try:
        import FreeCAD as App
        import Part
        import Sketcher  # noqa: F401
        import PartDesign  # noqa: F401
    except Exception as exc:
        raise RuntimeError('This builder must be run by FreeCAD Python with PartDesign available') from exc
    return App, Part


def _f(params, key, default):
    try:
        return float(params.get(key, default))
    except Exception:
        return float(default)


def _derive(params):
    W = _f(params, 'overall_width', 42.0)
    H = _f(params, 'overall_height', 34.0)
    opening = _f(params, 'base_opening', 13.5)
    wall = _f(params, 'base_wall', 2.4)
    retention_depth = _f(params, 'retention_depth', 19.0)
    retention_throat = _f(params, 'retention_throat', 8.2)
    bridge_inner_drop = _f(params, 'bridge_inner_drop', 11.0)
    tip_r = _f(params, 'tip_radius', 2.2)
    transition_r = _f(params, 'transition_radius', 5.0)
    connector_r = _f(params, 'connector_radius', 3.2)
    chamfer_len = _f(params, 'tip_chamfer_length', 3.6)
    chamfer_angle = math.radians(_f(params, 'tip_chamfer_angle', 35.0))
    pad_depth = _f(params, 'pad_depth', 6.0)

    half_w = W / 2.0
    y_top = H / 2.0
    y_bot = -H / 2.0
    outer_top_r = max(0.6, min(transition_r, W * 0.14, H * 0.18))
    actual_tip_r = max(0.45, min(tip_r, W * 0.08, H * 0.10))
    cdx_raw = chamfer_len * math.cos(chamfer_angle)
    cdy_raw = chamfer_len * math.sin(chamfer_angle)
    cdx_outer = max(0.35, min(cdx_raw, W * 0.16, half_w - outer_top_r - 0.5))
    cdy_outer = max(0.25, min(cdy_raw, H * 0.16))

    # Publicly declared dependent delta: leg length is not an independent edit.
    # It couples the opening and wall edit to the deep retained cavity.
    leg_length = retention_depth - 0.35 * bridge_inner_drop - wall - actual_tip_r - 0.10 * opening
    leg_length = max(2.4, min(leg_length, retention_depth - actual_tip_r - 1.0))

    inner_top_y = y_top - wall
    inner_bottom_y = inner_top_y - retention_depth
    bottom_margin = max(wall + actual_tip_r + 0.8, 1.3)
    if inner_bottom_y < y_bot + bottom_margin:
        inner_bottom_y = y_bot + bottom_margin
    if inner_top_y > y_top - 0.7:
        inner_top_y = y_top - 0.7

    half_open = max(1.2, opening / 2.0)
    half_throat = max(0.9, retention_throat / 2.0)
    # The profile is a single ordered boundary with a top-entering retention pocket,
    # so the top opening must remain inside the rounded outer top shoulders.
    max_inner_x = min(half_w - wall - 1.1, half_w - outer_top_r - 0.8)
    half_open = min(half_open, max_inner_x)
    half_throat = min(half_throat, half_open - 0.6)
    half_throat = max(0.9, half_throat)

    # Chamfer tangent endpoints are derived and clamped only enough to preserve
    # the single-boundary closure and the explicit connector/tip branch geometry.
    root_clear = max(0.45, (half_open - half_throat) * 0.45)
    inner_ch_dx = min(cdx_raw, root_clear)
    inner_ch_dx = min(inner_ch_dx, max(0.30, half_open - half_throat - 0.35))
    inner_ch_dx = max(0.25, inner_ch_dx)
    inner_ch_dy = min(cdy_raw, max(0.45, (inner_top_y - inner_bottom_y) * 0.20))
    p4x = half_throat + inner_ch_dx
    p4y = inner_bottom_y + actual_tip_r + inner_ch_dy
    p5x = half_throat
    p5y = inner_bottom_y + actual_tip_r

    root_room_x = max(0.35, half_open - p4x - 0.15)
    vertical_room = max(0.50, (inner_top_y - p4y) * 0.28)
    actual_connector_r = max(0.35, min(connector_r, root_room_x * 0.82, vertical_room))
    connector_y = inner_top_y - leg_length
    min_connector_y = p4y + actual_connector_r + 0.55
    max_connector_y = inner_top_y - actual_connector_r - 0.35
    connector_y = max(min_connector_y, min(max_connector_y, connector_y))

    return {
        'W': W, 'H': H, 'pad_depth': pad_depth, 'opening': opening, 'wall': wall,
        'retention_depth': retention_depth, 'retention_throat': retention_throat,
        'half_w': half_w, 'y_top': y_top, 'y_bot': y_bot,
        'outer_top_r': outer_top_r, 'tip_r': actual_tip_r,
        'cdx_outer': cdx_outer, 'cdy_outer': cdy_outer,
        'inner_top_y': inner_top_y, 'inner_bottom_y': inner_bottom_y,
        'half_open': half_open, 'half_throat': half_throat,
        'inner_ch_dx': inner_ch_dx, 'inner_ch_dy': inner_ch_dy,
        'p4x': p4x, 'p4y': p4y, 'p5x': p5x, 'p5y': p5y,
        'connector_r': actual_connector_r, 'connector_y': connector_y,
        'leg_length': leg_length,
        'tip_chamfer_angle_deg': math.degrees(chamfer_angle),
        'connector_root_branch': str(params.get('connector_root_branch', 'positive lower-intersection root')),
        'tip_arc_branch': str(params.get('tip_arc_branch', 'inner-to-outer tangent arc')),
    }


def _add_property(obj, typ, name, value):
    if name not in obj.PropertiesList:
        obj.addProperty(typ, name, 'TB3', 'tb3 public semantic metadata')
    setattr(obj, name, value)


def _line_geom(App, Part, a, b):
    return Part.LineSegment(App.Vector(a[0], a[1], 0), App.Vector(b[0], b[1], 0))


def _arc_geom(App, Part, center, radius, a0, a1):
    return Part.ArcOfCircle(
        Part.Circle(App.Vector(center[0], center[1], 0), App.Vector(0, 0, 1), radius),
        a0,
        a1,
    )


def _edge_from_line(App, Part, a, b):
    return _line_geom(App, Part, a, b).toShape()


def _edge_from_arc(App, Part, center, radius, a0, a1):
    return _arc_geom(App, Part, center, radius, a0, a1).toShape()


def _dist2(a, b):
    dx = a[0] - b[0]
    dy = a[1] - b[1]
    return dx * dx + dy * dy


def _angle(center, point):
    return math.atan2(point[1] - center[1], point[0] - center[0])


def _right_lower_tangent_point(center, point, radius):
    # Root-selected analytic connector construction.  The selected branch is the
    # public positive lower-intersection root: from the external chamfer endpoint
    # to the connector circle, choose the tangent point with the lower y value;
    # right/left connector branches are then mirrored for exact symmetry.
    cx, cy = center
    px, py = point
    vx = px - cx
    vy = py - cy
    d2 = vx * vx + vy * vy
    if d2 <= radius * radius:
        raise RuntimeError('Connector tangent root is not real for the selected parameters')
    root = math.sqrt(max(0.0, d2 - radius * radius))
    bx = cx + (radius * radius / d2) * vx
    by = cy + (radius * radius / d2) * vy
    scale = radius * root / d2
    # Perpendicular branch pair.  The lower-y option is the specified branch.
    o1 = (bx + scale * (-vy), by + scale * vx)
    o2 = (bx - scale * (-vy), by - scale * vx)
    if o1[1] < o2[1] - 1e-10:
        return o1
    if o2[1] < o1[1] - 1e-10:
        return o2
    return o1 if o1[0] >= o2[0] else o2


def _profile_segments(App, Part, params):
    d = _derive(params)
    W = d['W']
    H = d['H']
    half_w = d['half_w']
    yT = d['y_top']
    yB = d['y_bot']
    rt = d['outer_top_r']
    tr = d['tip_r']
    cdx = d['cdx_outer']
    cdy = d['cdy_outer']

    p_top_l = (-half_w + rt, yT)
    p_top_r = (half_w - rt, yT)
    p_r_u = (half_w, yT - rt)
    p_r_ch0 = (half_w, yB + tr + cdy)
    c_br = (half_w - cdx - tr, yB + tr)
    p_r_ch1 = (c_br[0] + tr, c_br[1])
    p_r_bot = (c_br[0], yB)
    c_bl = (-half_w + cdx + tr, yB + tr)
    p_l_bot = (c_bl[0], yB)
    p_l_ch1 = (c_bl[0] - tr, c_bl[1])
    p_l_ch0 = (-half_w, yB + tr + cdy)
    p_l_u = (-half_w, yT - rt)

    ho = d['half_open']
    ht = d['half_throat']
    yi = d['inner_top_y']
    yb = d['inner_bottom_y']
    rr = d['connector_r']
    yc = d['connector_y']
    ichdx = d['inner_ch_dx']
    ichdy = d['inner_ch_dy']

    q_l_open_top = (-ho, yT)
    q_l_inner = (-ho, yi)
    q_r_inner = (ho, yi)
    q_r_open_top = (ho, yT)

    c_conn_r = (ho - rr, yc)
    c_conn_l = (-(ho - rr), yc)
    p2 = (ho, yc)
    p11 = (-ho, yc)
    p4 = (ht + ichdx, yb + tr + ichdy)
    p5 = (ht, yb + tr)
    c_tip_r = (ht - tr, yb + tr)
    p6 = (ht - tr, yb)
    p7 = (-ht + tr, yb)
    c_tip_l = (-ht + tr, yb + tr)
    p8 = (-ht, yb + tr)
    p9 = (-(ht + ichdx), yb + tr + ichdy)

    t_r = _right_lower_tangent_point(c_conn_r, p4, rr)
    t_l = (-t_r[0], t_r[1])
    a_t_r = _angle(c_conn_r, t_r)
    if a_t_r > 0.0:
        a_t_r -= 2.0 * math.pi
    a_t_l = _angle(c_conn_l, t_l)
    if a_t_l < math.pi:
        a_t_l += 2.0 * math.pi

    segs = []

    def add_line(a, b, name):
        if _dist2(a, b) <= 1e-12:
            raise RuntimeError('Degenerate line segment in analytic profile: ' + name)
        segs.append({'kind': 'line', 'a': a, 'b': b, 'name': name})

    def add_arc(center, radius, a0, a1, name):
        if radius <= 0.0 or abs(a1 - a0) <= 1e-9:
            raise RuntimeError('Degenerate arc in analytic profile: ' + name)
        segs.append({'kind': 'arc', 'center': center, 'radius': radius, 'a0': a0, 'a1': a1, 'name': name})

    # One ordered, non-self-intersecting boundary.  The deep retention pocket is
    # explicitly part of the same wire rather than a second loop, so the final Pad
    # resolves to one valid solid while preserving all analytic root/tip features.
    add_line(p_top_l, q_l_open_top, 'top shoulder left')
    add_line(q_l_open_top, q_l_inner, 'left opening wall from declared wall')
    add_line(q_l_inner, p11, 'left derived leg length')
    add_arc(c_conn_l, rr, math.pi, a_t_l, 'left connector root branch arc')
    add_line(t_l, p9, 'left connector tangent leg')
    add_line(p9, p8, 'left derived chamfer tangent endpoint')
    add_arc(c_tip_l, tr, math.pi, 1.5 * math.pi, 'left rounded analytic tip section')
    add_line(p7, p6, 'retention throat bottom')
    add_arc(c_tip_r, tr, -0.5 * math.pi, 0.0, 'right rounded analytic tip section')
    add_line(p5, p4, 'right derived chamfer tangent endpoint')
    add_line(p4, t_r, 'right connector tangent leg')
    add_arc(c_conn_r, rr, a_t_r, 0.0, 'right connector root branch arc')
    add_line(p2, q_r_inner, 'right derived leg length')
    add_line(q_r_inner, q_r_open_top, 'right opening wall from declared wall')
    add_line(q_r_open_top, p_top_r, 'top shoulder right')
    add_arc((half_w - rt, yT - rt), rt, math.pi / 2.0, 0.0, 'right rounded transition')
    add_line(p_r_u, p_r_ch0, 'outer right side')
    add_line(p_r_ch0, p_r_ch1, 'outer right chamfer')
    add_arc(c_br, tr, 0.0, -math.pi / 2.0, 'outer right rounded tip')
    add_line(p_r_bot, p_l_bot, 'outer bottom')
    add_arc(c_bl, tr, -math.pi / 2.0, -math.pi, 'outer left rounded tip')
    add_line(p_l_ch1, p_l_ch0, 'outer left chamfer')
    add_line(p_l_ch0, p_l_u, 'outer left side')
    add_arc((-half_w + rt, yT - rt), rt, math.pi, math.pi / 2.0, 'left rounded transition')

    edges = []
    geoms = []
    for s in segs:
        if s['kind'] == 'line':
            edges.append(_edge_from_line(App, Part, s['a'], s['b']))
            geoms.append(_line_geom(App, Part, s['a'], s['b']))
        else:
            edges.append(_edge_from_arc(App, Part, s['center'], s['radius'], s['a0'], s['a1']))
            geoms.append(_arc_geom(App, Part, s['center'], s['radius'], s['a0'], s['a1']))

    wire = Part.Wire(edges)
    if not wire.isClosed():
        raise RuntimeError('Analytic profile wire is not closed before Sketcher creation')
    face = Part.Face(wire)
    if face.isNull():
        raise RuntimeError('Analytic profile did not produce a non-null face')
    if not face.isValid():
        raise RuntimeError('Analytic profile face is invalid or self-intersecting')
    if face.Area <= 0.0:
        raise RuntimeError('Analytic profile face has non-positive area')

    d['profile_area'] = float(face.Area)
    d['connector_tangent_right_x'] = float(t_r[0])
    d['connector_tangent_right_y'] = float(t_r[1])
    d['connector_tangent_left_x'] = float(t_l[0])
    d['connector_tangent_left_y'] = float(t_l[1])
    d['profile_segment_count'] = len(segs)
    d['single_wire_profile'] = True
    return geoms, d


def _find_xy_plane(body):
    try:
        for obj in body.Origin.OriginFeatures:
            if getattr(obj, 'Role', '') == 'XY_Plane' or obj.Name.endswith('XY_Plane') or obj.Label == 'XY_Plane':
                return obj
    except Exception:
        pass
    try:
        return body.Origin.OriginFeatures[0]
    except Exception as exc:
        raise RuntimeError('Unable to locate Body origin XY plane for explicit datum attachment') from exc


def _attach_to_datum(App, body, datum, sketch):
    xy = _find_xy_plane(body)
    attached_datum = False
    for attr in ('AttachmentSupport', 'Support'):
        try:
            setattr(datum, attr, [(xy, '')])
            datum.MapMode = 'FlatFace'
            attached_datum = True
            break
        except Exception:
            continue
    if not attached_datum:
        raise RuntimeError('Could not attach explicit datum plane to Body origin XY plane')
    datum.Placement = App.Placement(App.Vector(0, 0, 0), App.Rotation())

    attached_sketch = False
    for attr in ('AttachmentSupport', 'Support'):
        try:
            setattr(sketch, attr, [(datum, '')])
            sketch.MapMode = 'FlatFace'
            attached_sketch = True
            break
        except Exception:
            continue
    if not attached_sketch:
        raise RuntimeError('Could not attach profile sketch to explicit datum plane')


def _make_sketch(App, Part, doc, body, params, state_name):
    geoms, derived = _profile_segments(App, Part, params)
    datum = body.newObject('PartDesign::Plane', 'Explicit_Datum_Profile_Plane')
    sketch = body.newObject('Sketcher::SketchObject', 'Datum_Attached_Deep_Retention_Profile')
    _attach_to_datum(App, body, datum, sketch)

    for geom in geoms:
        sketch.addGeometry(geom, False)

    _add_property(body, 'App::PropertyString', 'DirectionSignature', 'freecad-v3-s06 deep_bridge chamfered_tip opening_wall')
    _add_property(body, 'App::PropertyString', 'BaseToEditOperator', 'declared opening-and-wall operator; feature chain preserved')
    _add_property(sketch, 'App::PropertyString', 'ConnectorRootBranch', derived['connector_root_branch'])
    _add_property(sketch, 'App::PropertyString', 'TipArcBranch', derived['tip_arc_branch'])
    _add_property(sketch, 'App::PropertyFloat', 'DerivedLegLength', float(derived['leg_length']))
    _add_property(sketch, 'App::PropertyFloat', 'DerivedChamferTangentEndpointX', float(derived['p4x']))
    _add_property(sketch, 'App::PropertyFloat', 'DerivedChamferTangentEndpointY', float(derived['p4y']))
    _add_property(sketch, 'App::PropertyFloat', 'ConnectorTangentRootX', float(derived['connector_tangent_right_x']))
    _add_property(sketch, 'App::PropertyFloat', 'ConnectorTangentRootY', float(derived['connector_tangent_right_y']))
    _add_property(sketch, 'App::PropertyFloat', 'ValidatedAnalyticProfileArea', float(derived['profile_area']))
    _add_property(sketch, 'App::PropertyString', 'ProfileSemantics', 'one ordered closed analytic sketch boundary: rounded outer transitions plus deep top-entering closed retention pocket with connector roots and chamfered rounded tip sections')
    _add_property(sketch, 'App::PropertyString', 'VariantStateName', state_name)
    return sketch, datum, derived


def _body_objects(doc):
    return [obj for obj in doc.Objects if getattr(obj, 'TypeId', '') == 'PartDesign::Body']


def _validate_document(doc):
    doc.recompute()
    bodies = _body_objects(doc)
    if len(bodies) != 1:
        raise RuntimeError('Final document must contain exactly one PartDesign Body')
    body = bodies[0]
    tip = getattr(body, 'Tip', None)
    if tip is None:
        raise RuntimeError('PartDesign Body has no complete final Tip')
    if getattr(tip, 'TypeId', '') != 'PartDesign::Pad':
        raise RuntimeError('Final Body Tip must be the native PartDesign::Pad')
    if not hasattr(tip, 'Shape') or tip.Shape.isNull():
        raise RuntimeError('Final Pad Tip has a null shape')
    if not tip.Shape.isValid():
        raise RuntimeError('Final Pad Tip shape is invalid')
    solids = list(tip.Shape.Solids)
    if len(solids) != 1:
        raise RuntimeError('PartDesign Pad did not produce exactly one solid')
    if solids[0].isNull() or (not solids[0].isValid()) or solids[0].Volume <= 0.0:
        raise RuntimeError('PartDesign Pad produced an invalid or non-positive-volume solid')
    body_solids = list(body.Shape.Solids)
    if len(body_solids) != 1:
        raise RuntimeError('PartDesign Body does not expose exactly one final solid')
    if body.Tip != tip:
        raise RuntimeError('PartDesign Body Tip is not persistent')
    return body, tip


def _new_clean_doc(App, name):
    try:
        old = App.getDocument(name)
        if old is not None:
            App.closeDocument(name)
    except Exception:
        pass
    return App.newDocument(name)


def _save_close_reopen_validate(App, doc, path):
    doc.recompute()
    _validate_document(doc)
    name = doc.Name
    doc.saveAs(path)
    App.closeDocument(name)
    reopened = App.openDocument(path)
    reopened.recompute()
    _validate_document(reopened)
    reopened.saveAs(path)
    return reopened


def _build_one(params, path, state_name):
    App, Part = _import_freecad()
    doc_name = 'tb3_s06_' + state_name
    doc = _new_clean_doc(App, doc_name)
    body = doc.addObject('PartDesign::Body', 'Body')
    sketch, datum, derived = _make_sketch(App, Part, doc, body, dict(params), state_name)
    doc.recompute()

    pad = body.newObject('PartDesign::Pad', 'Pad')
    pad.Profile = sketch
    pad.Length = float(derived['pad_depth'])
    pad.Midplane = True
    pad.Reversed = False
    body.Tip = pad
    _add_property(pad, 'App::PropertyString', 'FinalTipSemantics', 'complete final Body Tip generated by native PartDesign::Pad')
    _add_property(pad, 'App::PropertyString', 'DeepRetentionTopology', 'single ordered deep closed retention pocket topology; not ordinary bridge and not a second opaque feature')
    _add_property(pad, 'App::PropertyString', 'DependentDeltas', json.dumps({
        'leg_length': derived['leg_length'],
        'chamfer_endpoint_x': derived['p4x'],
        'chamfer_endpoint_y': derived['p4y'],
        'connector_root_x': derived['connector_tangent_right_x'],
        'connector_root_y': derived['connector_tangent_right_y'],
        'opening': derived['opening'],
        'wall': derived['wall']
    }, sort_keys=True))
    return _save_close_reopen_validate(App, doc, path)


def _write_answer_py(output_dir):
    target = os.path.join(output_dir, 'answer.py')
    source = None
    here = globals().get('__file__')
    if here and os.path.exists(here):
        with open(here, 'r', encoding='utf-8') as handle:
            source = handle.read()
    if not source or 'def build_pair(' not in source:
        raise RuntimeError('Unable to materialize complete builder source into answer.py')
    with open(target, 'w', encoding='utf-8') as handle:
        handle.write(source)
    return target


def build_pair(base_parameters, edit_parameters, output_dir='/app'):
    os.makedirs(output_dir, exist_ok=True)
    answer_py = _write_answer_py(output_dir)
    base_path = os.path.join(output_dir, 'answer_base.FCStd')
    edit_path = os.path.join(output_dir, 'answer_edit.FCStd')
    base_doc = _build_one(base_parameters, base_path, 'base')
    edit_doc = _build_one(edit_parameters, edit_path, 'edit')
    _validate_document(base_doc)
    _validate_document(edit_doc)
    return {
        'answer_py': answer_py,
        'answer_base': base_path,
        'answer_edit': edit_path,
        'direction_id': 'freecad-v3-s06',
        'feature_chain': 'PartDesign Body -> explicit datum plane -> datum-attached analytic Sketch -> native PartDesign::Pad -> complete Body Tip',
        'profile_convention': 'one ordered prevalidated Part.Wire/Part.Face boundary, no solver constraints moving computed endpoints',
        'base_derived': _derive(base_parameters),
        'edit_derived': _derive(edit_parameters),
    }
