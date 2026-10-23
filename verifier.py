import argparse
import json
import re
import sys
from dataclasses import dataclass
from typing import Any


CODE_BLOCK_RE = re.compile(r"```(?P<language>[\w.+-]*)\n(?P<code>[\s\S]+?)\n```")
VALID_DECISIONS = {"continue", "retry"}


@dataclass(frozen=True)
class CodeBlock:
    language: str
    code: str


def extract_code_blocks(text: str) -> list[CodeBlock]:
    """Return fenced code blocks from markdown-like input."""
    return [
        CodeBlock(
            language=match.group("language").strip(),
            code=match.group("code").strip(),
        )
        for match in CODE_BLOCK_RE.finditer(text)
        if match.group("code").strip()
    ]


def verify_codeblocks(
    text: str,
    client: Any | None = None,
    model: str | None = None,
) -> str:
    """
    Ask the configured model whether code-block metrics are acceptable.

    Returns exactly "continue" or "retry". Missing code blocks, empty model
    responses, and non-standard model responses all fail closed as "retry".
    """
    code_blocks = extract_code_blocks(text)
    if not code_blocks:
        return "retry"
    if client is None:
        from azure_openai_client import MODEL_NAME, create_client

        client = create_client()
        if model is None:
            model = MODEL_NAME
    if model is None:
        raise ValueError("A model name is required when passing a custom client.")

    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a conservative quantitative risk gate for code or output "
                    "used in capital allocation workflows. Return exactly one lowercase "
                    "word: continue or retry. Return retry if the code blocks contain "
                    "quantitative metrics that appear arithmetically wrong, internally "
                    "inconsistent, unsupported by the code, affected by data leakage, "
                    "overfit, based on unrealistic assumptions, missing downside risk "
                    "controls, or otherwise too risky for capital allocation. Return "
                    "continue only when the presented quantitative logic and metrics "
                    "look coherent enough for the next review step. Do not give "
                    "investment advice, explanations, markdown, punctuation, or extra text."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "task": (
                            "Review these fenced code blocks and decide whether the "
                            "quantitative metrics are coherent enough to continue."
                        ),
                        "allowed_outputs": sorted(VALID_DECISIONS),
                        "code_blocks": [
                            {"language": block.language, "code": block.code}
                            for block in code_blocks
                        ],
                    }
                ),
            },
        ],
    )

    content = response.choices[0].message.content
    if content is None:
        return "retry"

    decision = content.strip().lower()
    return decision if decision in VALID_DECISIONS else "retry"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify fenced code blocks for capital-allocation metric risk."
    )
    parser.add_argument(
        "input_file",
        nargs="?",
        help="Markdown/text file to verify. Reads stdin when omitted.",
    )
    args = parser.parse_args()

    if args.input_file:
        with open(args.input_file, encoding="utf-8") as file:
            text = file.read()
    else:
        text = sys.stdin.read()

    print(verify_codeblocks(text))


if __name__ == "__main__":
    main()
