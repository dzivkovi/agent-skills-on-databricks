"""
Job task: read an input document from the INBOX volume, transform it with an
inside-Databricks LLM, and write the result to the OUTBOX (deliverables) volume.

This is the "input bucket -> output bucket" pattern:
    /Volumes/workspace/genai/inbox/*.md   (a user drops a document here)
        -> LLM running inside Databricks (no external API key)
    /Volumes/workspace/genai/deliverables/*.md   (the user downloads the result)

MVP-0 note: this does NOT yet run an agentskills.io skill. It calls the LLM with
a fixed instruction to prove the whole pipeline. MVP-1 replaces the instruction
below with the branded-pptx skill (re-cut in python-pptx) and writes a .pptx.

Auth is AMBIENT: inside a Databricks job the WorkspaceClient authenticates as the
job's identity automatically. No secrets in this file.
"""
import argparse
import datetime
import os

import requests
from databricks.sdk import WorkspaceClient


def extract_text(message: dict) -> str:
    """Return plain text from a chat message, regardless of model output shape.

    - Most models: message["content"] is a string.
    - gpt-oss (reasoning): message["content"] is a list of parts
      (a "reasoning" part plus a "text" answer part).
    - GLM: chain-of-thought lands in message["reasoning_content"].
    Handling all three means swapping the model never breaks this job.
    """
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    if isinstance(content, list):
        answer = "\n".join(p.get("text", "") for p in content if p.get("type") != "reasoning")
        if answer.strip():
            return answer.strip()
    return (message.get("reasoning_content") or "").strip() or "(model returned no text)"


def call_llm(w: WorkspaceClient, model: str, messages: list, max_tokens: int = 1200) -> str:
    host = w.config.host.rstrip("/")
    headers = {**w.config.authenticate(), "Content-Type": "application/json"}
    resp = requests.post(
        f"{host}/serving-endpoints/{model}/invocations",
        headers=headers,
        json={"messages": messages, "max_tokens": max_tokens},
        timeout=180,
    )
    resp.raise_for_status()
    return extract_text(resp.json()["choices"][0]["message"])


def main():
    parser = argparse.ArgumentParser(description="Convert an inbox document into a deliverable.")
    parser.add_argument("--model", default="databricks-gpt-oss-120b")
    parser.add_argument("--in-path", default="/Volumes/workspace/genai/inbox/weekly-update.md")
    parser.add_argument("--out-dir", default="/Volumes/workspace/genai/deliverables")
    args = parser.parse_args()

    # 1) READ the input document from the inbox volume (normal file IO; volumes
    #    are mounted into the job's filesystem at /Volumes/...).
    with open(args.in_path, "r", encoding="utf-8") as f:
        source = f.read()

    # 2) TRANSFORM it with the inside-Databricks LLM. (In MVP-1 this instruction
    #    is replaced by the branded-pptx skill.)
    messages = [
        {"role": "system", "content": "You are a precise executive editor. Output clean markdown only, no preamble."},
        {"role": "user", "content": (
            "Turn the following raw team notes into a polished weekly executive summary: "
            "a title line, a one-sentence TL;DR, then 5 tight bullets grouped as Shipped / "
            "Metrics / Blocked-Next. Preserve the specific numbers. Use hyphens, not em-dashes.\n\n"
            f"RAW NOTES:\n{source}"
        )},
    ]
    body = call_llm(w=WorkspaceClient(), model=args.model, messages=messages)

    # 3) WRITE the deliverable to the outbox volume for the user to download.
    today = datetime.date.today().isoformat()
    stem = os.path.splitext(os.path.basename(args.in_path))[0]
    os.makedirs(args.out_dir, exist_ok=True)
    out_path = f"{args.out_dir}/{stem}-summary-{today}.md"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(
            f"# Executive Summary - {today}\n\n"
            f"_Source: `{os.path.basename(args.in_path)}` | Generated inside Databricks by "
            f"`{args.model}` (no external API key)._\n\n"
            f"{body}\n"
        )
    print(f"READ  {args.in_path}")
    print(f"WROTE {out_path}")


if __name__ == "__main__":
    main()
