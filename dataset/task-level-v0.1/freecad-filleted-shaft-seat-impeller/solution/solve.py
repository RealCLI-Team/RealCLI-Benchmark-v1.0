from canonical_builder import build_pair

BASE_PARAMETERS = {'blade_count': 9, 'blade_mid_angle': 23.0, 'blade_mid_radius': 63.0, 'blade_mid_width': 7.4, 'blade_mid_z': 15.8, 'blade_root_angle': 0.0, 'blade_root_radius': 35.0, 'blade_root_width': 9.2, 'blade_root_z': 9.0, 'blade_tip_angle': 46.0, 'blade_tip_radius': 88.0, 'blade_tip_width': 5.6, 'blade_tip_z': 12.0, 'bore_diameter': 22.0, 'hub_axial_thickness': 20.0, 'hub_outer_radius': 92.0, 'hub_root_radius': 18.0, 'shaft_seat_fillet_radius': 2.8, 'shaft_seat_height': 15.0, 'shaft_seat_outer_diameter': 36.0, 'shaft_seat_wall_thickness': 7.0}
EDIT_PARAMETERS = {'blade_count': 9, 'blade_mid_angle': 23.0, 'blade_mid_radius': 63.0, 'blade_mid_width': 7.4, 'blade_mid_z': 15.8, 'blade_root_angle': 0.0, 'blade_root_radius': 35.0, 'blade_root_width': 9.2, 'blade_root_z': 9.0, 'blade_tip_angle': 46.0, 'blade_tip_radius': 88.0, 'blade_tip_width': 5.6, 'blade_tip_z': 12.0, 'bore_diameter': 30.0, 'hub_axial_thickness': 20.0, 'hub_outer_radius': 92.0, 'hub_root_radius': 18.0, 'shaft_seat_fillet_radius': 2.8, 'shaft_seat_height': 15.0, 'shaft_seat_outer_diameter': 39.6, 'shaft_seat_wall_thickness': 4.8}

if __name__ == '__main__':
    build_pair(BASE_PARAMETERS, EDIT_PARAMETERS, output_dir='/app')
