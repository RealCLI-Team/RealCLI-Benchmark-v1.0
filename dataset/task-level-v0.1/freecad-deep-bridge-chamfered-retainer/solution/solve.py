from canonical_builder import build_pair

BASE_PARAMETERS = {'base_opening': 15.8, 'base_wall': 2.1, 'bridge_inner_drop': 13.6, 'connector_radius': 2.8, 'connector_root_branch': 'positive lower-intersection root', 'overall_height': 37.0, 'overall_width': 47.5, 'pad_depth': 5.5, 'retention_depth': 22.5, 'retention_throat': 7.4, 'tip_arc_branch': 'inner-to-outer tangent arc', 'tip_chamfer_angle': 40.0, 'tip_chamfer_length': 4.4, 'tip_radius': 1.9, 'transition_radius': 4.4}
EDIT_PARAMETERS = {'base_opening': 18.4, 'base_wall': 2.65, 'bridge_inner_drop': 13.6, 'connector_radius': 2.8, 'connector_root_branch': 'positive lower-intersection root', 'overall_height': 37.0, 'overall_width': 47.5, 'pad_depth': 5.5, 'retention_depth': 22.5, 'retention_throat': 7.4, 'tip_arc_branch': 'inner-to-outer tangent arc', 'tip_chamfer_angle': 40.0, 'tip_chamfer_length': 4.4, 'tip_radius': 1.9, 'transition_radius': 4.4}

if __name__ == '__main__':
    build_pair(BASE_PARAMETERS, EDIT_PARAMETERS, output_dir='/app')
