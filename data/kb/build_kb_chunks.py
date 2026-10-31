"""Rebuild kb_chunks.jsonl from the handbook Markdown (edit the .md, then run this).

    python data/kb/build_kb_chunks.py

Format expected in the Markdown:
    # Part F — Fees and finance
    ## F.3 Late fees and consequences of dues
    ### F.3.1 | Title
    **Type:** rule · **Applies to:** ... · **Status:** OFFICIAL · **Source:** ...
    **Questions:** question one | question two
    **Keywords:** a, b, c
    <body>
Everything from "# Index of questions" onwards is ignored.
"""
import json
import re
from pathlib import Path

HERE = Path(__file__).parent
MD = HERE / "AIDTM_Student_Handbook_KB_v3.md"
OUT = HERE / "kb_chunks.jsonl"
LEGACY = HERE / "legacy_map.json"

PART = re.compile(r"^# Part ([A-Z]) — (.+)$")
SEC = re.compile(r"^## ([A-Z]\.\d+) (.+)$")
CHUNK = re.compile(r"^### ([A-Z](?:\.\d+)+) \| (.+)$")
META = re.compile(r"^\*\*Type:\*\* (.+?) · \*\*Applies to:\*\* (.+?) · \*\*Status:\*\* (\S+) · \*\*Source:\*\* (.+)$")
VALID_STATUS = {"OFFICIAL", "OFFICIAL-DERIVED", "AUGMENTED"}


def parse(text: str) -> list[dict]:
    legacy = json.loads(LEGACY.read_text()) if LEGACY.exists() else {}
    inv: dict[str, list[str]] = {}
    for old, new in legacy.items():
        inv.setdefault(new, []).append(old)
    chunks, cur, part, part_title, sections = [], None, None, None, {}
    for line in text.split("\n"):
        if line.startswith("# Index of questions"):
            break
        if m := PART.match(line):
            if cur: chunks.append(cur); cur = None          # part intro text belongs to no chunk
            part, part_title = m.group(1), m.group(2).strip(); continue
        if m := SEC.match(line):
            if cur: chunks.append(cur); cur = None
            sections[m.group(1)] = m.group(2).strip(); continue
        if m := CHUNK.match(line):
            if cur: chunks.append(cur)
            cid = m.group(1)
            secnum = ".".join(cid.split(".")[:2]) if cid.count(".") >= 2 else cid.split(".")[0]
            cur = {"id": cid, "part": part, "part_title": part_title, "section": secnum, "section_title": sections.get(secnum, part_title),
                   "title": m.group(2).strip(), "lines": []}
            continue
        if cur is None or line.strip() == "---":
            continue
        if m := META.match(line):
            cur.update(type=m.group(1).strip(), applies_to=m.group(2).strip(), status=m.group(3), source=m.group(4).strip()); continue
        if line.startswith("**Questions:**"):
            cur["questions"] = [q.strip() for q in line[len("**Questions:**"):].split(" | ") if q.strip()]; continue
        if line.startswith("**Keywords:**"):
            cur["keywords"] = [k.strip() for k in line[len("**Keywords:**"):].split(",") if k.strip()]; continue
        cur["lines"].append(line)
    if cur: chunks.append(cur)
    for c in chunks:
        c["text"] = "\n".join(c.pop("lines")).strip()
        c["legacy_ids"] = sorted(inv.get(c["id"], []))
        c["word_count"] = len(c["text"].split())
        assert c.get("status") in VALID_STATUS, f"{c['id']}: bad or missing status"
        assert c.get("questions"), f"{c['id']}: missing questions"
        assert c["text"], f"{c['id']}: empty body"
    ids = [c["id"] for c in chunks]
    assert len(ids) == len(set(ids)), "duplicate IDs"
    return chunks


if __name__ == "__main__":
    chunks = parse(MD.read_text(encoding="utf-8"))
    order = ["id", "part", "part_title", "section", "section_title", "title", "type", "applies_to", "status", "source", "questions", "keywords", "text", "legacy_ids", "word_count"]
    with open(OUT, "w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps({k: c.get(k) for k in order}, ensure_ascii=False) + "\n")
    print(f"{len(chunks)} chunks -> {OUT.name}")
