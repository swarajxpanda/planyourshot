from langchain_core.messages import HumanMessage, SystemMessage
from llm import get_llm

SCREENPLAY_SNIPPET = """\
INT. COFFEE SHOP - NIGHT

SARAH sits alone at a table. She looks at her phone.

A message appears: "WE NEED TO TALK."

Sarah looks toward the door. JOHN enters.
"""

def main() -> None:
    llm = get_llm()
    messages = [
        SystemMessage(content=(
            "You are a film cinematographer. In 3-4 sentences, describe how "
            "you would shoot this screenplay moment."
        )),
        HumanMessage(content=SCREENPLAY_SNIPPET),
    ]
    response = llm.invoke(messages)
    print(response.text)


if __name__ == "__main__":
    main()