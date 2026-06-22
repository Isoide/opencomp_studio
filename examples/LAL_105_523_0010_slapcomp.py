# OpenComp Studio script editor / CLI setup script.
#
# In the app:
#   Paste this into Script Editor and Run.
#
# Headless:
#   python -m opencomp.cli --new --run-script examples/LAL_105_523_0010_slapcomp.py --save E:\opencomp_tests\LAL_105_523_0010\LAL_105_523_0010_slapcomp.opencomp
#
# Render after saving:
#   python -m opencomp.cli E:\opencomp_tests\LAL_105_523_0010\LAL_105_523_0010_slapcomp.opencomp --render Write_EXR --range 1001-1010

import glob
import re


SHOT_ROOT = r"E:\opencomp_tests\LAL_105_523_0010"

PLATE_PATH = rf"{SHOT_ROOT}\PLATE\LAL_105_523_0010_####.exr"
MAIN_3D_PATH = rf"{SHOT_ROOT}\3D\LAL_105_523_0010_3D_v003.####.exr"
CLOTHES_3D_PATH = rf"{SHOT_ROOT}\3D\LAL_105_523_0010_3D_CLOTHES_01.####.exr"
WRITE_PATH = rf"{SHOT_ROOT}\OUT\LAL_105_523_0010_SLAPCOMP_OPENCOMP_####.exr"

# Adjust these if your source format differs.
ROOT_WIDTH = 4096
ROOT_HEIGHT = 3024
WORKING_COLORSPACE = "ACES2065-1"
READ_COLORSPACE = "ACES2065-1"


def detect_frame_range(pattern, fallback=(1001, 1010)):
    frame_regex = re.compile(r"(\d+)(?=\.[^.\\/]+$)")
    frames = []
    for path in glob.glob(pattern.replace("####", "*")):
        match = frame_regex.search(path.replace("\\", "/"))
        if match:
            frames.append(int(match.group(1)))
    if not frames:
        return fallback
    return min(frames), max(frames)


def set_knobs(node, **values):
    for key, value in values.items():
        node.value(key).setValue(value)
    return node


def remove_existing_graph():
    for node in list(opencomp.nodes()):
        node.delete()


first_frame, last_frame = detect_frame_range(PLATE_PATH)
remove_existing_graph()

root = opencomp.node("root")
root.value("name").setValue("LAL_105_523_0010_slapcomp")
root.value("first_frame").setValue(first_frame)
root.value("last_frame").setValue(last_frame)
root.value("width").setValue(ROOT_WIDTH)
root.value("height").setValue(ROOT_HEIGHT)
root.value("working_colorspace").setValue(WORKING_COLORSPACE)
root.value("proxy_enabled").setValue(True)
root.value("viewer_max_width").setValue(1280)
root.value("viewer_max_height").setValue(720)
root.value("default_output_path").setValue(WRITE_PATH)

plate_read = opencomp.create_node(
    "Read",
    name="Plate_Read",
    position=(-360, -620),
    path=PLATE_PATH,
    colorspace=READ_COLORSPACE,
    frame_start=first_frame,
    frame_end=last_frame,
    before="hold",
    after="hold",
    missing_frames="error",
    auto_alpha=True,
)
plate_format = opencomp.create_node(
    "Reformat",
    name="Plate_Format",
    position=(-360, -470),
    width=ROOT_WIDTH,
    height=ROOT_HEIGHT,
    resize="distort",
)
plate_format.setInput("in", plate_read)

main_3d_read = opencomp.create_node(
    "Read",
    name="Main3D_Read",
    position=(40, -620),
    path=MAIN_3D_PATH,
    colorspace=READ_COLORSPACE,
    frame_start=first_frame,
    frame_end=last_frame,
    before="hold",
    after="hold",
    missing_frames="error",
    auto_alpha=True,
)
main_3d_format = opencomp.create_node(
    "Reformat",
    name="Main3D_Format",
    position=(40, -470),
    width=ROOT_WIDTH,
    height=ROOT_HEIGHT,
    resize="distort",
)
main_3d_grade = opencomp.create_node(
    "Grade",
    name="Main3D_Grade",
    position=(40, -330),
    gain=1.0,
    offset=0.0,
    gamma=1.0,
)
main_3d_format.setInput("in", main_3d_read)
main_3d_grade.setInput("in", main_3d_format)

clothes_read = opencomp.create_node(
    "Read",
    name="Clothes3D_Read",
    position=(430, -620),
    path=CLOTHES_3D_PATH,
    colorspace=READ_COLORSPACE,
    frame_start=first_frame,
    frame_end=last_frame,
    before="hold",
    after="hold",
    missing_frames="error",
    auto_alpha=True,
)
clothes_format = opencomp.create_node(
    "Reformat",
    name="Clothes3D_Format",
    position=(430, -470),
    width=ROOT_WIDTH,
    height=ROOT_HEIGHT,
    resize="distort",
)
clothes_grade = opencomp.create_node(
    "Grade",
    name="Clothes3D_Grade",
    position=(430, -330),
    gain=1.0,
    offset=0.0,
    gamma=1.0,
)
clothes_format.setInput("in", clothes_read)
clothes_grade.setInput("in", clothes_format)

merge_main = opencomp.create_node(
    "Merge",
    name="Merge_Main3D_Over_Plate",
    position=(-120, -150),
    operation="over",
    mix=1.0,
    bbox="union",
    metadata_from="b",
)
merge_main.setInput("b", plate_format)
merge_main.setInput("a", main_3d_grade)

merge_clothes = opencomp.create_node(
    "Merge",
    name="Merge_Clothes_Over_Main",
    position=(-120, 20),
    operation="over",
    mix=1.0,
    bbox="union",
    metadata_from="b",
)
merge_clothes.setInput("b", merge_main)
merge_clothes.setInput("a", clothes_grade)

final_grade = opencomp.create_node(
    "Grade",
    name="Final_SlapGrade",
    position=(-120, 190),
    gain=1.0,
    offset=0.0,
    gamma=1.0,
)
final_grade.setInput("in", merge_clothes)

viewer = opencomp.create_node(
    "Viewer",
    name="Viewer1",
    position=(-120, 370),
    active_input="1",
)
viewer.setInput("1", final_grade)
viewer.setInput("2", plate_format)
viewer.setInput("3", merge_main)
viewer.setInput("4", main_3d_grade)
viewer.setInput("5", clothes_grade)

write = opencomp.create_node(
    "Write",
    name="Write_EXR",
    position=(-120, 550),
    path=WRITE_PATH,
    channels="rgba",
    overwrite=True,
    create_directories=True,
    metadata="all",
)
write.setInput("in", final_grade)

print("Created LAL_105_523_0010 slapcomp")
print(f"Frame range: {first_frame}-{last_frame}")
print(f"Viewer inputs: 1 final, 2 plate, 3 plate+main3D, 4 main3D, 5 clothes3D")
print(f"Write node: {write.id} -> {WRITE_PATH}")
