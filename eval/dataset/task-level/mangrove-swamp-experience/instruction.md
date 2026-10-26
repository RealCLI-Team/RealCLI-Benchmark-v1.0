**Project Brief: Interactive Mangrove Swamp Experience**

**Objective**  
Create an immersive, real‑time interactive environment that simulates a mangrove swamp. The experience should highlight the complexity of mangrove root systems, dynamic water behavior, and hidden marine life, delivering both visual fidelity and intuitive user interaction.

**Core Requirements**

1. **Root‑System Visualization**  
   - High‑detail 3D models of mangrove roots with realistic bark textures.  
   - Interactive exposure: users can navigate around, under, and over the roots; roots react visually (e.g., subtle sway) to user proximity.  

2. **Procedural Water Ripple Shaders**  
   - Real‑time shader that generates ripples based on user movement, object interactions, and wind parameters.  
   - Support for layered ripple propagation (surface, subsurface) and realistic light refraction.

3. **Parallax Layers for Hidden Fish Schools**  
   - Multi‑layered depth system that reveals schools of fish when viewed from different angles or when light shafts penetrate the water column.  
   - Fish schools should animate organically, responding to water currents and user presence.

4. **Ambient Audio & Lighting**  
   - Spatialized wildlife sounds (bird calls, insects, distant fauna).  
   - Dynamic light shafts filtering through canopy, reacting to time‑of‑day settings.

5. **Performance & Compatibility**  
   - Target 90 FPS on high‑end VR hardware (e.g., Oculus Quest Pro, Valve Index) and ≥60 FPS on mid‑range PC.  
   - Optimize asset streaming to keep memory footprint within platform limits.

**Deliverables**

- Full 3D scene package with all assets, shaders, and scripts.  
- Documentation covering setup, performance tuning, and interaction controls.  
- Build for both standalone VR and PC‑based VR platforms.  

**Milestones & Timeline**

| Milestone | Description | Due |
|-----------|-------------|-----|
| M1 | Concept art, layout, and root‑system topology | 2 weeks |
| M2 | Procedural water ripple and shader implementation | 4 weeks |
| M3 | Fish behavior, lighting, and ambient audio | 6 weeks |
| M4 | VR interaction, optimization, and performance testing | 8 weeks |
| M5 | Final polish, documentation, and platform builds | 10 weeks |

**Interaction**

- Users can move freely through the swamp using VR controllers or standard desktop controls.
- Touching the water or moving through shallow areas should create visible ripples.
- Users can approach roots, fish, and other environmental elements to trigger subtle reactions.
- A simple time-of-day control can transition between morning, afternoon, sunset, and night.

**Visual Details**

- Include muddy water, floating debris, wet roots, leaves, and small environmental details.
- Add subtle water reflections, shadows, fog, and floating particles to create depth.
- Fish should occasionally disappear behind roots or into darker areas of the water.

**Performance**

- Use level-of-detail models, instancing, and culling for dense vegetation.
- Reduce shader and particle complexity automatically on lower-end hardware.
- Maintain stable frame rates during movement through dense root areas.