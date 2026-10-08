"""Resolve the local default tooling image to an immutable ID without pulling."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass

from ctfbot.runtime.docker import validate_image_reference


@dataclass(frozen=True, slots=True)
class RuntimeImageChoice:
    image: str = ""
    message: str = ""


def resolve_runtime_image() -> RuntimeImageChoice:
    # Only the selected general toolbox is selected automatically. Old fixture
    # images remain explicit choices for their accepted service profiles.
    reference = "ctfbot-tools:candidate"
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", "--format", '{{.Id}}\n{{json (index .Config "Volumes")}}\n{{json (index .Config "Labels")}}', reference],
            capture_output=True, text=True, timeout=3, check=False,
        )
    except FileNotFoundError:
        return RuntimeImageChoice(message="Docker CLI is unavailable. Install/configure Docker, then preview again.")
    except subprocess.TimeoutExpired:
        return RuntimeImageChoice(message="Docker image lookup timed out. Check Docker access, then preview again.")
    except OSError:
        return RuntimeImageChoice(message="Docker image lookup failed. Check Docker access, then preview again.")
    if result.returncode != 0:
        return RuntimeImageChoice(message="No local general toolbox is available. Check Docker access; build and accept ctfbot-tools:candidate following doc/phase-d/tool-image.md, then preview again, or select a pinned image manually.")
    try:
        image, volumes, labels_json = result.stdout.strip().split("\n", 2)
        validate_image_reference(image)
        if json.loads(volumes) not in (None, {}):
            raise ValueError("anonymous volumes are unsupported")
        labels = json.loads(labels_json)
        if not isinstance(labels, dict) or labels.get("org.ctfbot.tooling.profile") != "general-v2":
            raise ValueError("expected the general-v2 toolbox")
    except (ValueError, TypeError):
        return RuntimeImageChoice(message="Local toolbox is outdated or invalid. Build and accept general-v2 following doc/phase-d/tool-image.md, then preview again, or select a pinned image manually.")
    return RuntimeImageChoice(image=image, message="General CTF toolbox selected automatically. Preview the challenge before running.")
