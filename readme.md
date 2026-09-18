# LumiAvatar

LumiAvatar is a desktop avatar project with three cooperating tools:

* A PyQt5 **rig editor** for authoring PNG-puppet rigs (bones, hierarchy, image
  placement).
* A PyQt5 **animation editor** for keyframing rotations on a rig and saving the
  result as a separate animation file.
* A PyQt6 **desktop renderer** that plays a rig (and, eventually, an animation)
  as a borderless, transparent, always-on-top window with physics-driven motion.

## Requirements

- Python 3
- Windows is recommended for the global F9 hotkey used by the renderer.
- PyQt5 for the rig editor and animation editor
- PyQt6 for the desktop renderer

Install the dependencies from the project folder:

```powershell
pip install -r requirements.txt
```

## Project files

- `model_editor.py` is the PyQt5 rig authoring tool.
- `animation_editor.py` is the PyQt5 keyframe animation authoring tool.
- `model_desktop_render.py` is the PyQt6 runtime renderer.
- `assets/` contains saved rig JSON files, including the default
  `assets/save3.json`.
- `faces/` contains PNG artwork used by the rigs.
- `requirements.txt` lists the Qt dependencies.

The renderer can create `.image_bounds.json` beside the loaded rig. This is an
image bounds cache and can be deleted if the source PNG dimensions or
transparency change.

## Create or edit a rig

Run the editor from the project folder:

```powershell
python model_editor.py
```

The editor has three areas:

- **Left panel:** editing modes, skeleton hierarchy, and selected-bone
  properties.
- **Center canvas:** the rig, joints, and PNG artwork.
- **Right panel:** the global image Z-order manager.

### Editing modes

- **Move Joint (Assembly):** move a selected joint. Child joints move in their
  parent's local coordinates.
- **Move Image Offset (Pivot):** move the selected PNG without moving its
  joint.
- **Pose (Rotate Joint FK):** rotate a joint while its descendants follow
  through forward kinematics.

The canvas supports mouse-wheel zoom and middle-button pan. The skeleton
hierarchy supports adding, renaming, and deleting bones. Deleting a bone also
deletes all of its children.

### Image properties

Each bone can have multiple PNGs used as expressions. The editor supports:

- Expression selection
- Horizontal and vertical flips
- Image base rotation from `-360` to `360` degrees
- Per-bone maximum pose rotation, stored as `rotation_limit`
- Image pivot offsets
- A global image Z-level independent of the bone hierarchy
- Removing a single expression from a bone, or un-attaching every image

Use **Load Rig from JSON** to open an existing rig and **Save / Export JSON**
to write one. The exported file uses this structure:

```json
{
    "bones": {
        "BoneName": {
            "parent": null,
            "local_x": 0.0,
            "local_y": 0.0,
            "rotation": 0.0,
            "rotation_limit": 360.0,
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

Image paths are stored exactly as selected. Keep the referenced PNG files
available when reopening a rig.

## Author an animation

Run the animation editor from the project folder:

```powershell
python animation_editor.py
```

The animation editor has three areas:

- **Left panel:** editing modes (defaulted to POSE), the skeleton hierarchy,
  and per-bone properties (read-only display of expressions / flips / base
  rotation / Z).
- **Center canvas:** the rig, joints, and PNG artwork, with the same wheel-zoom
  and middle-button pan as the rig editor.
- **Bottom panel:** a keyframe timeline.

The rig and the animation are kept in **separate files**:

- The **rig file** is loaded as read-only. The animation editor never writes
  back to it; all structural changes (add/remove bones, add/remove images,
  flips, base rotation, pivot) belong to `model_editor.py`.
- The **animation file** stores only keyframe data plus a per-bone
  "non-priority" label list. It can be reloaded, edited, and re-saved without
  touching the rig.

### Workflow

1. Click **Load Rig JSON** (left panel) and pick a `model_editor` export. The
   rig appears on the canvas.
2. Use the canvas in POSE mode to rotate bones into the desired pose.
3. Click **Capture Keyframe** (bottom toolbar). Every bone's current rotation
   is stored as a new keyframe row at the end of the timeline. The total
   duration shown at the bottom of the timeline is the sum of all per-row
   intervals.
4. Move a bone or two, capture again — the next keyframe is appended.
5. Each row exposes an editable interval (milliseconds), easing curve
   (`linear`, `ease-in`, `ease-out`, `ease-in-out`), and an `X` to delete. A
   checkbox at the left of each row marks the row as the **capture-overwrite
   target**: with a checkbox ticked, the next **Capture Keyframe** updates that
   row instead of appending. Ticking the same box again un-ticks it; only one
   row can be ticked at a time.
6. Right-click any row for **Recapture this keyframe** (snap the current pose
   into that specific frame) or **Delete this keyframe**.
7. Click **Play** to walk the timeline once. Click **Stop** to halt playback
   and restore the rig to the pose you had before pressing play.

### Non-priority bones

Right-click any bone in the Skeleton Hierarchy tree to toggle it between
**priority** (animation-driven) and **non-priority** (LLM-overridable).
Non-priority bones are saved into the animation JSON under the
`non_priority_bones` field.

The intended runtime contract is: when the renderer plays the animation,
non-priority bones follow the keyframe rotation as usual, but the moment the
LLM stream sends a `<rotate:NAME=ANGLE>` command for one of those bones, that
command wins until the LLM stops issuing commands for it. This lets you script
an animation but still let the LLM take control of a specific bone on demand.

Each animation file carries its own non-priority list, so the same bone can be
non-priority in one animation and priority in another.

### File format

The animation JSON looks like this:

```json
{
    "format": "lumi_avatar_animation",
    "version": 1,
    "rig_name": "save3",
    "rig_source_path": "C:/path/to/save3.json",
    "non_priority_bones": ["head", "Larm"],
    "keyframes": [
        {
            "interval_ms": 100,
            "easing": "linear",
            "rotations": {
                "torso": 0.0,
                "head": 20.0,
                "Larm": -15.0
            }
        },
        {
            "interval_ms": 200,
            "easing": "ease-in-out",
            "rotations": {
                "torso": 0.0,
                "head": -10.0,
                "Larm": 30.0
            }
        }
    ]
}
```

Keyframes are **dense**: each row stores the rotation of every bone in the
rig, even bones that didn't change. This keeps playback simple and the file
explicit about what every frame looks like. The total animation length is the
sum of every keyframe's `interval_ms`.

## Run the desktop avatar

Run the renderer from the project folder:

```powershell
python model_desktop_render.py
```

The renderer currently loads `assets/save3.json` by default. To use another
rig, change the `target_file` value near the bottom of
`model_desktop_render.py`.

The avatar runs as a borderless, transparent, always-on-top window. Drag it
with the left mouse button to throw it. The physics system applies gravity,
horizontal wall collisions, friction, bounce, and velocity-based limb movement.
Press **F9** to close the renderer on Windows.

### Easy physics tuning

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

## External controller script

The renderer starts a localhost command server on `127.0.0.1:8765`. Start the
renderer first, then run the random controller in a second terminal:

```powershell
python model_desktop_render.py
python random_controller.py
```

`random_controller.py` sends random rotations, face expressions, and model
movement commands while both programs run concurrently. Stop it with `Ctrl+C`.
Its options are:

```powershell
python random_controller.py --interval 1.0 --min-x 100 --max-x 1400
```

The controller understands these command tags:

```text
<rotate:head=20>
<expr:face=3>
<move:x=800,speed=400>
```

`<move:x=800,speed=400>` smoothly moves the entire avatar window to screen X
position `800` at `400` pixels per second. The speed is optional and defaults
to `300` pixels per second. The `PuppetController` method that performs the
same action inside Python is:

```python
controller.move_model_x(800, speed=400)
```
