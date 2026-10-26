**Project Title:** Dreamscape Voxel Survival Crafting Game  

**Objective:**  
Develop a single‑page, WebGL‑based voxel survival game (HTML5) that transports players to a dreamscape biome where block textures dynamically respond to the player’s imagination, enabling custom‑designed structures and unique visual effects each session.  

**Core Features**  

1. **Voxel World Engine**  
   - Procedurally generated terrain with a focus on a “dreamscape” biome (floating islands, surreal terrain, ethereal lighting).  
   - Real‑time block placement and destruction (break and place mechanics).  
   - Seamless infinite world loading/unloading to keep memory usage low.  

2. **Dynamic Texture Shifting**  
   - Implement a shader‑based system where block textures morph based on player input (e.g., mouse gestures, voice commands, or a simple UI ('✦ Enter the Dream ✦' button to start; press I for the Imagination panel with Hue/Saturation/Dreaminess sliders and a theme tag on top, press B for the editor, press T to trigger Shift Reality with a purple flash) “imagination” tool).  
   - Each session starts with a default palette that evolves as the player interacts, producing unique visual themes.  

3. **Custom Structure Builder**  
   - Provide an in‑game editor that lets players design and save custom structures (e.g., drag‑and‑drop, brush size, pattern tools).  
   - Persist custom structures locally (IndexedDB) or via optional cloud save.  

4. **Visual Effects**  
   - Particle systems for dream‑like phenomena (floating spores, light trails, ambient glow).  
   - Post‑processing effects (bloom, chromatic aberration) that react to texture changes.  

5. **Player Mechanics**  
   - First‑person controller with smooth movement, jumping, and crouching.  
   - Collision detection and physics for both terrain and entities.  
   - Spawn logic: player always spawns on a grassy surface within the biome.  

6. **User Interface**  
   - Minimalist HUD showing health, inventory, and imagination tool status.  
   - Pause/menu overlay with options: resume, settings (graphics, controls), and “Reset Dreamscape”.

7. **Survival Systems**
   - Hunger/energy meter that slowly depletes, restored by eating dream-fruit or resting at a campfire.

8. **Inventory**
   - Hotbar (8–9 slots) for quick block/tool selection.

9. **Scope**
   - Single biome (the dreamscape), no multiplayer, no combat enemies in v1 — focus is on building, exploring, and the texture-shifting imagination mechanic.
   - Playable in-browser with no install required, targeting modern desktop browsers first.