from pydantic import BaseModel, Field, field_validator



class DialogueLine(BaseModel):
    speaker: str = Field(description="The name of the character speaking the line.")
    text: str = Field(description="The dialogue spoken by the character, verbatim.")

class ActionLine(BaseModel):
    text: str = Field(description="One action or description from the scene")

class Scene(BaseModel):

    @field_validator("characters")
    @classmethod
    def titlecase_names(cls, v: list[str]) -> list[str]:
        return [name.strip().title() for name in v]
    
    @field_validator("int_ext")
    @classmethod
    def upper_int_ext(cls, v: str | None) -> str | None:
         return v.upper() if v else v
    
    scene_number: int = Field(description="Sequential scene number, starting at 1")
    int_ext: str | None = Field(
        default=None,
        description="INT or EXT from the scene heading, if present",
    )
    location: str | None = Field(
        default=None,
        description="Location from the scene heading, e.g. 'coffee shop'",
    )
    time_of_day: str | None = Field(
        default=None,
        description="Time from the heading, e.g. 'night', 'day', if present",
    )
    characters: list[str] = Field(
        default_factory=list,
        description="Distinct characters who appear or speak in this scene",
    )
    actions: list[ActionLine] = Field(
        default_factory=list,
        description="Action / stage-direction beats, in order",
    )
    dialogue: list[DialogueLine] = Field(
        default_factory=list,
        description="Spoken lines, in order",
    )
    camera_directions: list[str] = Field(
        default_factory=list,
        description="Camera / framing directions from the script, e.g. 'CLOSE UP ON EYES'",
    )
    transitions: list[str] = Field(
        default_factory=list,
        description="Editing transitions from the script, e.g. 'CUT TO:', 'MATCH CUT TO:'",
    )

    

class SceneBody(BaseModel):
    characters: list[str] = Field(default_factory=list,
        description="Distinct characters who appear or speak")
    actions: list[ActionLine] = Field(default_factory=list,
        description="Action / stage-direction beats, in order")
    dialogue: list[DialogueLine] = Field(default_factory=list,
        description="Spoken lines, in order")


class Screenplay(BaseModel):
    scenes: list[Scene] = Field(default_factory=list)