# LumiAvatar

LumiAvatar is a desktop avatar project with a PNG rig editor and a transparent,
physics-driven desktop renderer.

## Requirements

- Python 3
- Windows is recommended for the global F9 hotkey used by the renderer.
- PyQt5 for the editor
- PyQt6 for the desktop renderer

Install the dependencies from the project folder:

```powershell
pip install -r requirements.txt
```

## Project files

- `model_editor.py` is the PyQt5 rig authoring tool.
- `model_desktop_render.py` is the PyQt6 runtime renderer.
- `assets/` contains saved rig JSON files, including the default `assets/save3.json`.
- `faces/` contains PNG artwork used by the rigs.
- `requirements.txt` lists the Qt dependencies.

The renderer can create `.image_bounds.json` beside the loaded rig. This is an image
bounds cache and can be deleted if the source PNG dimensions or transparency change.

## Create or edit a rig

Run the editor from the project folder:

```powershell
python model_editor.py
```

The editor has three areas:

- **Left panel:** editing modes, skeleton hierarchy, and selected-bone properties.
- **Center canvas:** the rig, joints, and PNG artwork.
- **Right panel:** the global image Z-order manager.

### Editing modes

- **Move Joint (Assembly):** move a selected joint. Child joints move in their
  parent's local coordinates.
- **Move Image Offset (Pivot):** move the selected PNG without moving its joint.
- **Pose (Rotate Joint FK):** rotate a joint while its descendants follow through
  forward kinematics.

The canvas supports mouse-wheel zoom and middle-button pan. The skeleton hierarchy
supports adding, renaming, and deleting bones. Deleting a bone also deletes all of its
children.

### Image properties

Each bone can have multiple PNGs used as expressions. The editor supports:

- Expression selection
- Horizontal and vertical flips
- Image base rotation from `-360` to `360` degrees
- Image pivot offsets
- A global image Z-level independent of the bone hierarchy

Use **Load Rig from JSON** to open an existing rig and **Save / Export JSON** to write
one. The exported file uses this structure:

```json
{
    "bones": {
        "BoneName": {
            "parent": null,
            "local_x": 0.0,
            "local_y": 0.0,
            "rotation": 0.0,
            "image_paths": ["faces/example.png"],
            "expression_index": 0,
            "flip_h": false,
            "flip_v": false,
            "image_rotation": 0.0,
            "image_offset_x": 0.0,
            "image_offset_y": 0.0,
            "z_value": 0
        }
    }
}
```

Image paths are stored exactly as selected. Keep the referenced PNG files available
when reopening a rig.

## Run the desktop avatar

Run the renderer from the project folder:

```powershell
python model_desktop_render.py
```

The renderer currently loads `assets/save3.json` by default. To use another rig,
change the `target_file` value near the bottom of `model_desktop_render.py`.

The avatar runs as a borderless, transparent, always-on-top window. Drag it with the
left mouse button to throw it. The physics system applies gravity, horizontal wall
collisions, friction, bounce, and velocity-based limb movement. Press **F9** to close
the renderer on Windows.

## Easy physics tuning

The renderer's constants are at the top of `model_desktop_render.py`:

- `MODEL_SCALE`: display scale for the loaded rig.
- `MODEL_PADDING`: transparent padding around the rig window.
- `PHYSICS_FPS`: physics update rate.
- `GRAVITY`: downward acceleration.
- `FRICTION`: horizontal velocity retained after floor contact.
- `BOUNCE`: velocity multiplier after wall or floor contact.
- `MAX_LEAN` and `MAX_DROP`: limits for physics-driven bone rotation.
- `LEAN_FACTOR` and `DROP_FACTOR`: convert velocity into limb rotation.
- `THROW_STRENGTH`: converts drag velocity into throw velocity.
- `MAX_THROW_SPEED_X` and `MAX_THROW_SPEED_Y`: throw velocity limits.
- `PLATFORM_Y`: floor position in screen Y coordinates. It is currently `475`.
  Set it to `None` to use the bottom of the available screen instead.

The runtime also recognizes these control tags through `PuppetController`:

```text
<rotate:BoneName=45>
<expr:BoneName=1>
```

They rotate a named bone or select an expression index when passed to
`parse_llm_stream`.
