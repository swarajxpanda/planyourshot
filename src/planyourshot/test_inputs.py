from planyourshot.parse_scene import parse_scene, save_scene

CASES = {
    # normal: heading + action + on-screen text (dialogue should stay empty)
    "normal": """INT. COFFEE SHOP - NIGHT

SARAH sits alone at a table. She looks at her phone.

A message appears: "WE NEED TO TALK."

Sarah looks toward the door. JOHN enters.""",

    # no heading: int_ext / location / time_of_day should come back null
    "no_heading": """Sarah paces the room. She tears the letter in half.""",

    # real spoken dialogue: should populate the dialogue list with speakers
    "with_dialogue": """INT. KITCHEN - DAY

MAYA
You never listen to me.

DEV
That's not fair, and you know it.""",

    # garbage: not a screenplay at all — observe the failure mode
    "garbage": "Quarterly revenue exceeded projections by twelve percent this year.",
}


def main() -> None:
    for name, text in CASES.items():
        print(f"\n===== {name} =====")
        try:
            scene = parse_scene(text)
            print(scene.model_dump_json(indent=2))
            save_scene(scene, name=name)
        except Exception as e:
            print(f"FAILED: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()