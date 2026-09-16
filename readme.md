# Lumi Avatar Model Editor

`model_editor.py` is a PyQt5 desktop editor for building 2D PNG puppets. It lets you
## Starting the editor

From the project directory, run:
```powershell
python model_editor.py
```
The editor opens with three areas:

- **Left panel:** tools, skeleton hierarchy, selected-bone properties, and JSON actions.
- **Center canvas:** the editable puppet and joint markers.
- **Right panel:** the global PNG Z-order/layer stack.

The application uses a dark theme and starts with an empty rig unless a JSON file is
loaded.

## Basic workflow

1. Click **Add Bone** and enter a unique bone name.
2. Add a PNG to the selected bone with **Add PNG / Expression**.
3. Add more bones. If a bone is selected when **Add Bone** is used, the new bone is
	created as its child. Otherwise it is created as a root bone at the center of the
	canvas.
4. Use **Move Joint (Assembly)** to position bones and assemble the PNG parts.
5. Use **Move Image Offset (Pivot)** to align a PNG around its bone without moving the
	joint itself.
6. Use **Pose (Rotate Joint FK)** to rotate a bone. Its descendants follow the rotation
	through forward kinematics (FK).
7. Set expressions, flips, image rotation, and layer order in the selected-bone
	 properties.
8. Use **Save / Export JSON** to save the rig.

## Tools and canvas controls

### Move Joint (Assembly)

Left-drag the selected joint on the canvas to change its position. A child bone moves
in the selected parent's local coordinate system, while a root bone moves directly in
scene coordinates. Moving a parent also moves all descendant bones and their images.

### Move Image Offset (Pivot)

Left-drag the selected image to change its local image offset. This changes where the
PNG is drawn relative to its bone, which is useful for aligning a hand, face, hair, or
other part without changing the skeleton.

### Pose (Rotate Joint FK)

Left-drag around the selected joint to rotate it. The selected bone's rotation is
updated relative to the drag direction, and all child bones follow it. The rotation is
stored as the bone's `rotation` value in the exported JSON.

### Zoom and pan

- Use the mouse wheel to zoom in or out around the cursor.
- Hold the middle mouse button and drag to pan the canvas.
- The green dashed lines show parent-child bone connections.
- The selected bone is highlighted, and the HUD shows its local position, rotation, and
	image Z-level.

## Skeleton hierarchy

The **Skeleton Hierarchy** tree displays all bones and their parent-child relationships.

- **Add Bone:** creates a uniquely named bone. A selected bone becomes its parent.
- **Delete Bone:** deletes the selected bone and all of its descendants, including their
	PNG images.
- **Rename Selected Bone:** changes the name if the new name is not already used.
- Selecting a bone in the tree selects the corresponding joint on the canvas.
- Selecting a joint on the canvas updates the tree selection.

Bone hierarchy controls skeletal transforms. It does not force image draw order; image
stacking is controlled separately by Z-level.

## Selected bone properties

These controls apply to the currently selected bone.

### PNGs and expressions

**Add PNG / Expression** opens a file picker that accepts one or more PNG files. The
files are stored in the order selected. The first image is expression index `0`.

**Expression Index** selects which PNG is currently displayed for that bone. The control
automatically limits the valid index to the number of images assigned to the bone.
Expressions are alternate images for the same bone, such as eyes open/closed or
different mouth shapes.

### Image transforms

- **Flip Horizontal:** mirrors the selected PNG horizontally.
- **Flip Vertical:** mirrors the selected PNG vertically.
- **Image Base Rotation (Deg):** rotates the PNG around its image center before the
	bone's pose transform is applied. Values range from `-360` to `360` degrees.

The image offset is edited interactively with the **Move Image Offset (Pivot)** tool and
is saved as `image_offset_x` and `image_offset_y`.

### Image layer order

- **Bring Forward (Z+):** increases the selected PNG's global Z-level by `1`.
- **Send Backward (Z-):** decreases it by `1`.

A higher Z-level draws the PNG above every lower-Z PNG, even if the image belongs to a
parent or child elsewhere in the skeleton. This allows, for example, a hand image to
render behind a torso image.

## Z-Order Manager

The right sidebar lists every bone sorted from highest to lowest global image Z-level.
Each row contains:

- **Bone Name:** the bone represented by the image layer.
- **Z-Level:** an editable integer from `-9999` to `9999`.

Click a row to select that bone. Edit its Z-level directly to change the stacking order.
Click **Sort Stack (High to Low)** to rebuild the table in descending Z order. Z-levels
belong to PNG images, not to the small joint markers or the skeleton hierarchy.

## Loading and saving rigs

### Load Rig from JSON

**Load Rig from JSON** opens a `.json` file, clears the current editor contents, and
rebuilds the complete rig. Missing image files are skipped and reported in the terminal.
The file must contain a top-level `bones` object.

Loading applies bone hierarchy and transforms, loads available PNG paths, selects the
saved expression, restores flips and image rotation, restores the saved image offset,
and performs a final transform synchronization pass.

### Save / Export JSON

**Save / Export JSON** writes the current rig to a user-selected `.json` file. Each bone
is saved with these fields:

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

Field meanings:

- `parent`: parent bone name, or `null` for a root bone.
- `local_x`, `local_y`: bone position relative to its parent, or scene position for a
	root.
- `rotation`: bone pose rotation in degrees.
- `image_paths`: PNG files assigned to the bone, in expression order.
- `expression_index`: currently selected PNG index.
- `flip_h`, `flip_v`: horizontal and vertical mirror states.
- `image_rotation`: base rotation applied to the PNG.
- `image_offset_x`, `image_offset_y`: PNG pivot offset in bone-local coordinates.
- `z_value`: global image layer order.

The editor writes the image paths exactly as selected. Keep the PNG files at those
locations when moving or reopening a saved rig. Relative paths are interpreted relative
to the process working directory, so launching the editor from the project directory is
recommended.

## Important behavior

- Bone names must be unique because names identify bones in the tree, Z-order table, and
	JSON data.
- Deleting a parent also deletes every child below it.
- Loading a file replaces the current rig; export before loading if the current work must
	be preserved.
- The editor does not create or modify the PNG files. It stores their paths and display
	settings.
- `model_editor.py` is the authoring tool. The runtime avatar behavior is implemented in
	`Lumi_model.py`, which uses a model configuration file separately from editor saves.

## Requirements

Install the dependencies listed in `requirements.txt`, including PyQt5, in the active
Python environment before launching the editor.
# LumiAvatar Model Editor

`model_editor.py` is a desktop editor for building 2D puppet rigs from PNG image parts. It lets you create a bone hierarchy, attach one or more PNGs to each bone, adjust pivots and image transforms, pose the skeleton, control the global layer order, and save or reload the rig as JSON.

## Requirements

- Python 3
- PyQt5

Install the dependencies from the project folder:

```powershell
pip install -r requirements.txt
```

## Starting the editor

Run this command from the project folder:

```powershell
python model_editor.py
```

The window opens with three areas:

- **Left panel:** editing tools, skeleton hierarchy, and selected-bone properties.
- **Center canvas:** the rig and its PNG images.
- **Right panel:** the global PNG layer stack.

The editor uses a dark theme and opens at approximately 1500 x 900 pixels.

## Basic workflow

1. Click **Add Bone**.
2. Enter a unique bone name.
3. Add more bones. If a bone is selected when you add another bone, the new bone becomes its child; otherwise it becomes a root bone.
4. Select a bone and click **Add PNG / Expression** to choose one or more PNG files.
5. Use the tools and selected-bone properties to align each image.
6. Use the right-side **Z-Order Manager** to arrange which images appear in front.
7. Choose **Save / Export JSON** to save the rig.

## Tools

### Move Joint (Assembly)

Drag the selected bone on the canvas to move its joint. A child bone is moved in its parent's local coordinate system, while a root bone is moved in scene coordinates. The bone's descendants follow because they are part of the same hierarchy.

Use this mode to assemble the character and position joints such as the shoulders, elbows, hips, and knees.

### Move Image Offset (Pivot)

Drag the selected PNG independently of its bone joint. This changes the image offset, or pivot, in the selected bone's local coordinate system without changing the skeleton position.

Use this mode when the joint is correctly placed but the artwork needs to be shifted around that joint.

### Pose (Rotate Joint FK)

Drag the selected bone around its joint to rotate it. Rotation uses forward kinematics: descendants inherit the rotation of their parent bones.

Use this mode to preview poses such as bent arms, raised legs, or head turns. The rotation is stored in the exported JSON.

## Skeleton hierarchy

The **Skeleton Hierarchy** tree shows all root bones and their children.

- **Add Bone:** creates a new uniquely named bone. It becomes a child of the selected tree bone, or a root bone when nothing is selected. New child bones start at `(50, 50)` relative to their parent; new root bones start near the center of the canvas.
- **Rename Selected Bone:** changes the selected bone's name. Names must be unique and cannot be empty.
- **Delete Bone:** deletes the selected bone and its entire descendant subtree, including their PNG images.
- Selecting a bone in the tree selects the same bone on the canvas and updates its properties.
- Selecting a bone on the canvas updates the tree and the Z-order table.

Each bone is displayed as a colored joint marker. A green dashed line connects child joints to their parents. Selected joints are highlighted in red.

## Selected bone properties

These controls apply to the selected bone:

- **Add PNG / Expression:** opens a file picker that accepts PNG files. Selecting multiple files attaches them as expressions for that bone in the order returned by the file picker.
- **Expression Index:** selects which attached PNG is currently displayed. Index `0` is the first image. The range expands automatically as images are added.
- **Flip Horizontal:** mirrors the displayed PNG horizontally around its image center.
- **Flip Vertical:** mirrors the displayed PNG vertically around its image center.
- **Image Base Rotation (Deg):** rotates the PNG around its image center, from `-360` to `360` degrees. This is separate from the bone's pose rotation.
- **Bring Forward (Z+):** increases the selected image's global Z-level by `1`.
- **Send Backward (Z-):** decreases the selected image's global Z-level by `1`.

When the first image is added to a bone, it is initially centered on that bone. Further image offset adjustments are preserved when the rig is saved and loaded.

## Z-order manager

The right sidebar lists every bone's PNG image layer, sorted from highest Z-level to lowest.

- Click a row to select that bone.
- Edit the **Z-Level** spin box to set an exact value from `-9999` to `9999`.
- Higher values render on top of lower values.
- Z-order is global across the entire rig. It is independent of the skeleton hierarchy, so an image attached to a child bone can render behind an image attached to an ancestor bone.
- **Sort Stack (High to Low)** refreshes the table and restores the highest layer at the top.

## Canvas navigation

- **Mouse wheel:** zoom in or out around the cursor.
- **Middle-mouse drag:** pan the canvas.
- **Left-mouse drag:** manipulates the selected bone according to the active tool mode.

The green HUD in the upper-left of the canvas shows the selected bone's name, local position, bone rotation, and image Z-level.

## JSON save and load

### Exporting

Click **Save / Export JSON**, choose a destination, and save a `.json` file. The exported file has this structure:

```json
{
	"bones": {
		"BoneName": {
			"parent": null,
			"local_x": 0.0,
			"local_y": 0.0,
			"rotation": 0.0,
			"image_paths": ["path/to/image.png"],
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

The file records:

- parent-child relationships;
- each bone's local position and pose rotation;
- all PNG paths attached to the bone;
- the active expression;
- horizontal and vertical flips;
- base image rotation;
- image pivot offset;
- global image Z-level.

### Importing

Click **Load Rig from JSON** and select a `.json` file. The current rig is cleared first, then the saved bones, hierarchy, images, transforms, expressions, and layer order are reconstructed.

Image files must still exist at the paths stored in the JSON. Missing files are skipped and reported in the terminal, so keep the PNG files in place or update the paths before loading.

Invalid JSON displays an error dialog and does not load. A valid file may contain bones in any order; the editor performs a final transform synchronization after rebuilding the hierarchy.

## Important notes

- Loading a rig replaces the current unsaved rig.
- Deleting a bone also deletes all of its child bones from the current editor session.
- The editor does not copy PNG files into the project; JSON stores their original paths.
- A bone can exist without an image, but it will only contribute its joint and hierarchy transform until an image is attached.
- The editor is for authoring rig data. The runtime/display behavior is implemented separately in `Lumi_model.py`.

