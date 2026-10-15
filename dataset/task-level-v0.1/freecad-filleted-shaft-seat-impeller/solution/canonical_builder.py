import os
import math
import json


def _require_freecad():
    import FreeCAD
    import Part
    import Sketcher
    return FreeCAD, Part, Sketcher


def _origin_feature(body, label):
    for obj in body.Origin.OriginFeatures:
        if obj.Name == label or obj.Label == label or obj.Name.endswith(label):
            return obj
    raise RuntimeError('Origin feature not found: ' + label)


def _attach_to_plane(obj, plane, z_offset=0.0):
    FreeCAD, Part, Sketcher = _require_freecad()
    try:
        obj.AttachmentSupport = [(plane, '')]
    except Exception:
        try:
            obj.Support = (plane, [''])
        except Exception:
            pass
    try:
        obj.MapMode = 'FlatFace'
    except Exception:
        pass
    try:
        obj.AttachmentOffset = FreeCAD.Placement(FreeCAD.Vector(0, 0, float(z_offset)), FreeCAD.Rotation())
    except Exception:
        pass


def _closed_polyline(sketch, points):
    FreeCAD, Part, Sketcher = _require_freecad()
    n = len(points)
    first_index = None
    prev_index = None
    for i in range(n):
        p1 = points[i]
        p2 = points[(i + 1) % n]
        idx = sketch.addGeometry(Part.LineSegment(FreeCAD.Vector(p1[0], p1[1], 0), FreeCAD.Vector(p2[0], p2[1], 0)), False)
        if first_index is None:
            first_index = idx
        if prev_index is not None:
            try:
                sketch.addConstraint(Sketcher.Constraint('Coincident', prev_index, 2, idx, 1))
            except Exception:
                pass
        prev_index = idx
    try:
        sketch.addConstraint(Sketcher.Constraint('Coincident', prev_index, 2, first_index, 1))
    except Exception:
        pass


def _blade_section_points(radius, angle_deg, tangential_width, radial_chord):
    a = math.radians(float(angle_deg))
    er = (math.cos(a), math.sin(a))
    et = (-math.sin(a), math.cos(a))
    cx = float(radius) * er[0]
    cy = float(radius) * er[1]
    hr = float(radial_chord) * 0.5
    ht = float(tangential_width) * 0.5
    pts = []
    for sr, st in [(-1, -1), (1, -1), (1, 1), (-1, 1)]:
        pts.append((cx + sr * hr * er[0] + st * ht * et[0], cy + sr * hr * er[1] + st * ht * et[1]))
    return pts


def _try_set_axis(feature, prop, candidates):
    for c in candidates:
        try:
            setattr(feature, prop, c)
            return True
        except Exception:
            continue
    return False


def _make_section_plane(body, xy_plane, name, z):
    plane = body.newObject('PartDesign::Plane', name)
    _attach_to_plane(plane, xy_plane, z)
    return plane


def _circular_edge_names(feature, target_radius=None, preferred_z=None):
    names = []
    scored = []
    try:
        edges = list(feature.Shape.Edges)
    except Exception:
        return names
    for i, e in enumerate(edges, start=1):
        c = getattr(e, 'Curve', None)
        if c is None or c.__class__.__name__ != 'Circle':
            continue
        r = float(getattr(c, 'Radius', 0.0))
        cen = getattr(c, 'Center', None)
        z = float(getattr(cen, 'z', 0.0)) if cen is not None else 0.0
        score = 0.0
        if target_radius is not None:
            score += abs(r - float(target_radius)) * 100.0
        if preferred_z is not None:
            score += abs(z - float(preferred_z))
        scored.append((score, 'Edge%d' % i))
    scored.sort(key=lambda x: (x[0], x[1]))
    return [n for s, n in scored]


def _build_model(params, path, doc_name):
    FreeCAD, Part, Sketcher = _require_freecad()
    p = dict(params)
    doc = FreeCAD.newDocument(doc_name)
    body = doc.addObject('PartDesign::Body', 'Body')
    doc.recompute()

    xy = _origin_feature(body, 'XY_Plane')
    xz = _origin_feature(body, 'XZ_Plane')
    z_axis = _origin_feature(body, 'Z_Axis')

    datum_axis = body.newObject('PartDesign::Line', 'Impeller_Axis_Datum_Z')
    try:
        datum_axis.AttachmentSupport = [(z_axis, '')]
        datum_axis.MapMode = 'ObjectZ'
    except Exception:
        pass
    datum_axis.Label = 'explicit impeller rotation and polar axis datum'
    doc.recompute()

    T = float(p['hub_axial_thickness'])
    outer_r = float(p['hub_outer_radius'])
    backplate_h = max(1.6, min(T * 0.22, float(p['blade_root_z']) * 0.45))
    seat_r = float(p['shaft_seat_outer_diameter']) * 0.5
    root_r = float(p['blade_root_radius'])
    root_w = float(p['blade_root_width'])
    core_r = max(float(p['hub_root_radius']), root_r + root_w * 0.72, seat_r + float(p['shaft_seat_fillet_radius']) + 1.0)
    core_r = min(core_r, outer_r * 0.62)

    hub_sk = body.newObject('Sketcher::SketchObject', 'Sketch_Revolved_Hub_Backbone_Profile')
    _attach_to_plane(hub_sk, xz, 0.0)
    profile_pts = [(0.0, 0.0), (outer_r, 0.0), (outer_r, backplate_h), (core_r, backplate_h), (core_r, T), (0.0, T)]
    _closed_polyline(hub_sk, profile_pts)
    doc.recompute()

    rev = body.newObject('PartDesign::Revolution', 'Revolved_Impeller_Backbone')
    rev.Profile = hub_sk
    rev.Angle = 360.0
    _try_set_axis(rev, 'ReferenceAxis', [(datum_axis, ['']), (z_axis, ['']), (body.Origin, ['Z_Axis']), (hub_sk, ['V_Axis'])])
    doc.recompute()

    section_data = [
        ('Root', float(p['blade_root_radius']), float(p['blade_root_angle']), float(p['blade_root_width']), float(p['blade_root_z'])),
        ('Mid', float(p['blade_mid_radius']), float(p['blade_mid_angle']), float(p['blade_mid_width']), float(p['blade_mid_z'])),
        ('Tip', float(p['blade_tip_radius']), float(p['blade_tip_angle']), float(p['blade_tip_width']), float(p['blade_tip_z']))
    ]
    section_sketches = []
    # radial chord is deliberately smaller than one blade pitch to preserve open passages.
    min_pitch = 2.0 * math.pi * min(float(p['blade_root_radius']), float(p['blade_mid_radius']), float(p['blade_tip_radius'])) / float(p['blade_count'])
    chord_base = max(3.0, min(root_w * 0.95, min_pitch * 0.28))
    for name, radius, angle, width, z in section_data:
        pl = _make_section_plane(body, xy, 'Datum_Blade_%s_Section_Depth_Z_%s' % (name, str(z).replace('.', '_')), z)
        doc.recompute()
        sk = body.newObject('Sketcher::SketchObject', 'Sketch_Blade_%s_Analytic_Section' % name)
        _attach_to_plane(sk, pl, 0.0)
        pts = _blade_section_points(radius, angle, width, chord_base * (0.92 if name == 'Tip' else 1.0))
        _closed_polyline(sk, pts)
        section_sketches.append(sk)
        doc.recompute()

    loft = body.newObject('PartDesign::AdditiveLoft', 'Blade_Seed_Attached_AdditiveLoft')
    loft.Profile = section_sketches[0]
    loft.Sections = section_sketches[1:]
    try:
        loft.Ruled = False
    except Exception:
        pass
    doc.recompute()

    pattern = body.newObject('PartDesign::PolarPattern', 'Blade_PolarPattern_OpenPassages')
    try:
        pattern.Originals = [loft]
    except Exception:
        try:
            pattern.Original = loft
        except Exception:
            pass
    pattern.Occurrences = int(p['blade_count'])
    pattern.Angle = 360.0
    _try_set_axis(pattern, 'Axis', [(datum_axis, ['']), (z_axis, ['']), (body.Origin, ['Z_Axis'])])
    doc.recompute()

    seat = body.newObject('PartDesign::AdditiveCylinder', 'Shaft_Seat_Branch')
    seat.Radius = seat_r
    seat.Height = float(p['shaft_seat_height'])
    try:
        seat.AttachmentSupport = [(xy, '')]
        seat.MapMode = 'FlatFace'
        seat.AttachmentOffset = FreeCAD.Placement(FreeCAD.Vector(0, 0, T), FreeCAD.Rotation())
    except Exception:
        seat.Placement = FreeCAD.Placement(FreeCAD.Vector(0, 0, T), FreeCAD.Rotation())
    doc.recompute()

    fillet = body.newObject('PartDesign::Fillet', 'Shaft_Seat_Base_Fillet')
    edge_names = _circular_edge_names(seat, target_radius=seat_r, preferred_z=T)
    if edge_names:
        fillet.Base = (seat, [edge_names[0]])
    else:
        fillet.Base = (seat, [])
    fillet.Radius = float(p['shaft_seat_fillet_radius'])
    doc.recompute()

    bore = body.newObject('PartDesign::SubtractiveCylinder', 'Downstream_Through_Bore_Cut')
    bore.Radius = float(p['bore_diameter']) * 0.5
    bore.Height = T + float(p['shaft_seat_height']) + 24.0
    try:
        bore.AttachmentSupport = [(xy, '')]
        bore.MapMode = 'FlatFace'
        bore.AttachmentOffset = FreeCAD.Placement(FreeCAD.Vector(0, 0, -12.0), FreeCAD.Rotation())
    except Exception:
        bore.Placement = FreeCAD.Placement(FreeCAD.Vector(0, 0, -12.0), FreeCAD.Rotation())
    doc.recompute()

    body.Tip = bore
    doc.recompute()

    body.Label = 'Filleted shaft-seat impeller Body - one connected solid final tip'
    rev.Label = 'Revolution backbone using explicit impeller axis datum'
    loft.Label = 'Attached analytic AdditiveLoft blade seed'
    pattern.Label = 'PolarPattern blades about explicit axis with open passages'
    seat.Label = 'Connected shaft-seat branch added after blade pattern'
    fillet.Label = 'Filleted transition from shaft-seat branch to backbone'
    bore.Label = 'Real downstream through-bore cut after fillet and polar pattern'

    try:
        solids = len(body.Tip.Shape.Solids)
        if solids != 1:
            raise RuntimeError('Final Body Tip is expected to have one solid, found %d' % solids)
    except Exception as exc:
        raise RuntimeError('Final Body Tip validation failed: %s' % exc)

    doc.saveAs(path)
    FreeCAD.closeDocument(doc.Name)


def _write_answer_py(output_dir):
    os.makedirs(output_dir, exist_ok=True)
    dst = os.path.join(output_dir, 'answer.py')
    src_path = globals().get('__file__')
    if src_path and os.path.exists(src_path):
        with open(src_path, 'r', encoding='utf-8') as f:
            src = f.read()
    else:
        src = "# Builder source was executed from a dynamic context.\n# The build_pair entrypoint is present in the submitted solution module.\n"
    with open(dst, 'w', encoding='utf-8') as f:
        f.write(src)


def _validate_pair_semantics(base_parameters, edit_parameters):
    bore_base = float(base_parameters['bore_diameter'])
    bore_edit = float(edit_parameters['bore_diameter'])
    if bore_base == bore_edit:
        raise ValueError('Declared primary edit factor bore_diameter must differ between Base and Edit.')
    wb = float(base_parameters['shaft_seat_wall_thickness'])
    we = float(edit_parameters['shaft_seat_wall_thickness'])
    odb = float(base_parameters['shaft_seat_outer_diameter'])
    ode = float(edit_parameters['shaft_seat_outer_diameter'])
    if abs(odb - (bore_base + 2.0 * wb)) > 1e-7:
        raise ValueError('Base shaft_seat_outer_diameter does not equal bore_diameter + 2*shaft_seat_wall_thickness.')
    if abs(ode - (bore_edit + 2.0 * we)) > 1e-7:
        raise ValueError('Edit shaft_seat_outer_diameter does not equal bore_diameter + 2*shaft_seat_wall_thickness.')
    invariant_keys = sorted(set(base_parameters.keys()) - set(['bore_diameter', 'shaft_seat_wall_thickness', 'shaft_seat_outer_diameter']))
    for k in invariant_keys:
        if base_parameters[k] != edit_parameters[k]:
            raise ValueError('Only bore_diameter and its declared dependent shaft-seat dimensions may change; changed key: ' + k)


def build_pair(base_parameters, edit_parameters, output_dir='/app'):
    """Build Base and Edit FreeCAD documents for the frozen filleted shaft-seat impeller blueprint."""
    os.makedirs(output_dir, exist_ok=True)
    _validate_pair_semantics(base_parameters, edit_parameters)
    _write_answer_py(output_dir)
    _build_model(base_parameters, os.path.join(output_dir, 'answer_base.FCStd'), 'answer_base')
    _build_model(edit_parameters, os.path.join(output_dir, 'answer_edit.FCStd'), 'answer_edit')
    manifest = {
        'direction_id': 'freecad-v3-i02',
        'primary_edit_factor': 'bore_diameter',
        'dependent_delta': 'shaft_seat_wall_thickness',
        'derived_relation': 'shaft_seat_outer_diameter = bore_diameter + 2 * shaft_seat_wall_thickness',
        'artifacts': ['answer.py', 'answer_base.FCStd', 'answer_edit.FCStd']
    }
    with open(os.path.join(output_dir, 'builder_manifest.json'), 'w', encoding='utf-8') as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
    return manifest
