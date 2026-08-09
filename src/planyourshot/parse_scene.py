from pathlib import Path
from planyourshot.schema import Scene, SceneBody
from planyourshot.llm import get_llm

_extractor = get_llm().with_structured_output(Scene)
_body_extractor = get_llm().with_structured_output(SceneBody)

_BODY_PROMPT = (
    "Extract the characters, action lines, and spoken dialogue from this "
    "screenplay scene body. Extract only what is explicitly present — do not "
    "invent or interpret. If there are no actions or no dialogue, return empty "
    "lists rather than inventing content. On-screen text (phone messages, signs, "
    "titles) is action, not dialogue.\n\n"
)

_PROMPT = (
    "Extract this screenplay scene into the structured schema. "
    "Extract only what is explicitly on the page — do not invent or interpret.\n\n"
)


def parse_scene(text: str) -> Scene:
    return _extractor.invoke(_PROMPT + text)

def parse_body(text: str) -> SceneBody:
    return _body_extractor.invoke(_BODY_PROMPT + text)

def save_scene(scene: Scene, name: str | None = None, out_dir: str = "data/parsed") -> Path:
    path = Path(out_dir)
    path.mkdir(parents=True, exist_ok=True)
    stem = name or f"scene_{scene.scene_number:02d}"
    file = path / f"{stem}.json"
    file.write_text(scene.model_dump_json(indent=2), encoding="utf-8")
    return file