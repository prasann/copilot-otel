"""Local folder convention; callers must independently verify source workspace identity."""

from pathlib import Path


def project_identity(workspace: str, projects_root: str) -> dict[str, str]:
    path = Path(workspace)
    root = Path(projects_root)
    if not path.is_absolute() or not root.is_absolute():
        raise ValueError("Workspace and projects root must be absolute paths")
    try:
        relative = path.resolve().relative_to(root.resolve())
    except ValueError:
        return {
            "project.id": "unattributed",
            "attribution.status": "unattributed",
            "attribution.method": "outside_projects_root",
        }
    if len(relative.parts) < 2 or relative.parts[0] not in ("personal", "msft"):
        return {
            "project.id": "unattributed",
            "attribution.status": "unattributed",
            "attribution.method": "unsupported_project_layout",
        }
    category, name = relative.parts[:2]
    return {
        "project.id": f"{category}/{name}",
        "project.name": name,
        "project.category": category,
        "attribution.status": "attributed",
        "attribution.method": "projects_folder_v1",
    }
