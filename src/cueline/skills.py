"""Skill library: parse SKILL.md, import from agents (read-only), install into the AI agents on this computer.

Install is the only place Cueline writes outside its own folder. It always needs an explicit confirm from the UI,
backs up any existing SKILL.md into data/backups/, and never touches other files in the skill folder.
"""
import difflib
import os
import re
from datetime import datetime
from pathlib import Path

from . import db

HOME = Path.home()
AGENT_SKILLS_DIR = Path(os.environ.get("CUELINE_AGENT_SKILLS_DIR", HOME / "agent-skills"))
CLAUDE_SKILLS_DIR = Path(os.environ.get("CUELINE_CLAUDE_SKILLS_DIR", HOME / ".claude" / "skills"))
CODEX_SKILLS_DIR = Path(os.environ.get("CUELINE_CODEX_SKILLS_DIR",
                                       Path(os.environ.get("CODEX_HOME", HOME / ".codex")) / "skills"))
ACTIVITY_LOG = os.environ.get("CUELINE_ACTIVITY_LOG")  # optional: append one line per install to this file
IMPORT_ROOTS = {  # label -> folder scanned read-only for */SKILL.md (missing folders are skipped)
    "Shared (agent-skills)": AGENT_SKILLS_DIR,
    "Claude Code": CLAUDE_SKILLS_DIR,
    "Codex": CODEX_SKILLS_DIR,
    "Hermes": Path(os.environ.get("CUELINE_HERMES_SKILLS_DIR", HOME / ".hermes" / "skills")),
}
SKIP_DIRS = {"_quarantine", ".curator_backups", "__pycache__", ".git", "node_modules"}
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")


def parse(body: str) -> dict:
    """Return {name, description} from SKILL.md frontmatter (falls back to the H1)."""
    name = desc = ""
    m = re.match(r"^\s*---\n(.*?)\n---", body, re.S)
    if m:
        lines = m.group(1).splitlines()
        for i, line in enumerate(lines):
            if line.startswith("name:"):
                name = line.split(":", 1)[1].strip().strip("\"'")
            elif line.startswith("description:"):
                desc = line.split(":", 1)[1].strip()
                if desc in (">", "|", ">-", "|-", ">+", "|+", ""):  # YAML block scalar: indented lines follow
                    block = []
                    for nxt in lines[i + 1:]:
                        if nxt and not nxt[0].isspace():
                            break
                        block.append(nxt.strip())
                    desc = " ".join(b for b in block if b)
                desc = desc.strip("\"'")
    if not name:
        h1 = re.search(r"^#\s+(.+)$", body, re.M)
        name = h1.group(1) if h1 else "untitled-skill"
    return {"name": slugify(name), "description": desc}


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:64]
    return slug or "untitled-skill"


def _normalize_name(body: str, name: str) -> str:
    """Write the slugified name back into the frontmatter so installed copies match the folder name."""
    return re.sub(r"^(\s*---\n(?:.*\n)*?)name:[^\n]*", lambda m: f"{m.group(1)}name: {name}", body, count=1)


def create_from_markdown(body: str, origin: str, origin_ref: str | None = None) -> dict:
    meta = parse(body)
    body = _normalize_name(body, meta["name"])
    now = db.now()
    return db.insert("skills", name=meta["name"], description=meta["description"], body=body.strip() + "\n",
                     origin=origin, origin_ref=origin_ref, version=1, created_at=now, updated_at=now)


def save(skill_id: str, body: str) -> dict:
    current = db.one("SELECT * FROM skills WHERE id = ?", (skill_id,))
    if current is None:
        raise KeyError(skill_id)
    meta = parse(body)
    body = _normalize_name(body.strip() + "\n", meta["name"])
    if body == current["body"]:
        return current
    return db.update("skills", skill_id, body=body, name=meta["name"], description=meta["description"],
                     version=current["version"] + 1, updated_at=db.now())


# ---------- import (read-only) ----------
def importable() -> list[dict]:
    have = {(s["origin_ref"] or "") for s in db.skills()}
    out = []
    for label, root in IMPORT_ROOTS.items():
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("SKILL.md")):
            rel = path.relative_to(root)
            if any(part in SKIP_DIRS or part.startswith(".") for part in rel.parts[:-1]):
                continue
            try:
                meta = parse(path.read_text(encoding="utf-8", errors="replace")[:4000])
            except OSError:
                continue
            out.append({"path": str(path), "root": label, "folder": str(rel.parent), "name": meta["name"],
                        "description": meta["description"], "imported": str(path) in have})
    return out


def import_paths(paths: list[str]) -> list[dict]:
    allowed = {item["path"] for item in importable()}
    created = []
    for p in paths:
        if p not in allowed:
            raise ValueError(f"Not an importable skill: {p}")
        body = Path(p).read_text(encoding="utf-8", errors="replace")
        created.append(create_from_markdown(body, origin="imported", origin_ref=p))
    return created


# ---------- install ----------
def agent_dirs() -> dict[str, Path]:
    """Skill folders of the agents on this computer: label -> folder. The first holds the file, the rest link to it."""
    dirs = {}
    if "CUELINE_AGENT_SKILLS_DIR" in os.environ or AGENT_SKILLS_DIR.is_dir():
        dirs["Shared (agent-skills)"] = AGENT_SKILLS_DIR
    if CLAUDE_SKILLS_DIR.parent.is_dir():  # ~/.claude exists = Claude Code is installed
        dirs["Claude Code"] = CLAUDE_SKILLS_DIR
    if CODEX_SKILLS_DIR.parent.is_dir():
        dirs["Codex"] = CODEX_SKILLS_DIR
    return dirs


def install_preview(skill: dict) -> dict:
    name = skill["name"]
    if not NAME_RE.match(name):
        raise ValueError("Skill name must be kebab-case (a-z, 0-9, -). Edit the 'name:' line first.")
    dirs = agent_dirs()
    if not dirs:
        raise ValueError("No AI agent found on this computer (Claude Code or Codex). Download the skill instead.")
    (agent, home), *others = dirs.items()
    target = home / name / "SKILL.md"
    existing = target.read_text(encoding="utf-8") if target.exists() else None
    diff = ""
    if existing is not None:
        diff = "".join(difflib.unified_diff(existing.splitlines(True), skill["body"].splitlines(True),
                                            fromfile="installed", tofile="cueline"))
    links = [{"agent": label, "path": str(d / name), "linked": os.path.lexists(d / name)} for label, d in others]
    return {"agent": agent, "target": str(target), "exists": existing is not None,
            "identical": existing == skill["body"], "diff": diff, "links": links}


def install(skill: dict, links: list[str] | None = None) -> dict:
    """Write SKILL.md into the first agent's folder; link it into the other agents named in `links`."""
    preview = install_preview(skill)
    target = Path(preview["target"])
    if preview["exists"] and not preview["identical"]:
        backup_dir = db.DATA_DIR / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        (backup_dir / f"{skill['name']}-{stamp}.SKILL.md").write_text(target.read_text(encoding="utf-8"),
                                                                       encoding="utf-8")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(skill["body"], encoding="utf-8")
    linked = []
    for item in preview["links"]:
        if item["linked"]:
            linked.append(item["agent"])
        elif item["agent"] in (links or []):
            Path(item["path"]).parent.mkdir(parents=True, exist_ok=True)  # e.g. a fresh ~/.claude has no skills/ yet
            os.symlink(target.parent, item["path"])
            linked.append(item["agent"])
    _log_activity(skill, preview["exists"], target)
    db.update("skills", skill["id"], installed_path=str(target), installed_at=db.now())
    return {"path": str(target), "agent": preview["agent"], "updated": preview["exists"], "linked": linked}


def _log_activity(skill: dict, updated: bool, target: Path):
    if not ACTIVITY_LOG or not Path(ACTIVITY_LOG).exists():
        return
    verb = "Updated" if updated else "Installed"
    line = (f"| {datetime.now():%Y-%m-%d} | Cueline (user-approved) | agent-skills | {verb} skill "
            f"`{skill['name']}` v{skill['version']} from Cueline | `{target}` |\n")
    with open(ACTIVITY_LOG, "a", encoding="utf-8") as f:
        f.write(line)
