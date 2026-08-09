import sys
from pathlib import Path

from planyourshot.loader import load_text
from planyourshot.splitter import split_scenes
from planyourshot.parse_scene import parse_body
from planyourshot.schema import Scene, Screenplay


def parse_screenplay(path: str) -> Screenplay:
    raw = load_text(path)
    raw_scenes = split_scenes(raw)
    scenes: list[Scene] = []
    for rs in raw_scenes:
        body = parse_body(rs.body) if rs.body.strip() else None
        scene = Scene(
            scene_number=rs.scene_number,
            int_ext=rs.int_ext,
            location=rs.location,
            time_of_day=rs.time_of_day,
            camera_directions=rs.camera_directions,   # add
            transitions=rs.transitions,               # add
            **(body.model_dump() if body else {}),
        )
        scenes.append(scene)
    return Screenplay(scenes=scenes)


def save_screenplay(sp: Screenplay, name: str = "screenplay", out_dir: str = "data/parsed") -> Path:
    path = Path(out_dir)
    path.mkdir(parents=True, exist_ok=True)
    file = path / f"{name}.json"
    file.write_text(sp.model_dump_json(indent=2), encoding="utf-8")
    return file


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: uv run python src/pipeline.py <path-to-screenplay.txt|.pdf>")
        sys.exit(1)

    src_path = sys.argv[1]
    sp = parse_screenplay(src_path)
    print(f"Parsed {len(sp.scenes)} scenes from {src_path}\n")
    for s in sp.scenes:
        print(f"  Scene {s.scene_number}: {s.int_ext or '?'} {s.location or '?'} "
              f"- {s.time_of_day or '?'}  |  {len(s.actions)} actions, "
              f"{len(s.dialogue)} lines, {len(s.camera_directions)} camera, "
              f"{len(s.transitions)} transitions")
    out = save_screenplay(sp, name=Path(src_path).stem)
    print(f"\nSaved → {out}")