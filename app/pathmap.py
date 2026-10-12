"""Pfadzuordnung zwischen Containern: "Pfad bei Emby/Radarr/Sonarr => Pfad hier", eine Zeile je Zuordnung."""
import os

from . import config


def _rules(key):
    rules = []
    for line in (config.get(key) or "").replace(";", "\n").splitlines():
        if "=>" not in line:
            continue
        remote, local = (x.strip().rstrip("/\\") for x in line.split("=>", 1))
        if remote and local:
            rules.append((remote, local))
    return sorted(rules, key=lambda r: len(r[0]), reverse=True)     # längster Präfix zuerst


def _swap(path, rules, reverse=False):
    if not path:
        return path
    p = path.replace("\\", "/") if not reverse else path
    for remote, local in rules:
        src, dst = (local, remote) if reverse else (remote.replace("\\", "/"), local)
        if p == src or p.startswith(src + "/"):
            out = dst + p[len(src):]
            return out if not reverse or "\\" not in dst else out.replace("/", "\\")
    return path


def to_local(system, path):
    """system: emby | radarr | sonarr"""
    return os.path.normpath(_swap(path, _rules(f"{system}_path_map"))) if path else path


def to_remote(system, path):
    return _swap(path, _rules(f"{system}_path_map"), reverse=True) if path else path
