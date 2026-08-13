#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import hmac
import http.client
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import time
import urllib.parse
import uuid
import zipfile


IGNORED_DIRS = {
    ".git", ".github", ".idea", ".vscode", "node_modules", "target",
    "dist", "coverage", ".angular", ".cache", "__pycache__",
    "veracode-enterprise", ".gitlab", ".gradle", "build", "out"
}

JS_ALLOWED_SUFFIXES = {
    ".asp", ".cjs", ".css", ".ehtml", ".es", ".es6", ".handlebars",
    ".hbs", ".hjs", ".htm", ".html", ".js", ".jsx", ".json", ".jsp",
    ".map", ".mjs", ".mustache", ".php", ".ts", ".tsx", ".vue",
    ".xhtml", ".yaml", ".yml"
}

JS_IMPORTANT_FILES = {
    "package.json", "package-lock.json", "angular.json", "tsconfig.json",
    "tsconfig.app.json", "nx.json", "workspace.json", "project.json"
}

MAINFRAME_SUFFIXES = {
    ".cbl", ".cob", ".cobol", ".cpy", ".copy", ".cpb", ".inc",
    ".pco", ".jcl", ".bms", ".pli", ".pl1", ".asm", ".s", ".mac",
    ".txt", ".sql", ".dcl", ".proc"
}
MAINFRAME_EXCLUDED_SUFFIXES = {
    ".zip", ".jar", ".war", ".ear", ".exe", ".dll", ".class", ".pyc",
    ".png", ".jpg", ".jpeg", ".gif", ".pdf", ".doc", ".docx", ".xls",
    ".xlsx", ".log", ".tmp"
}
SENSITIVE_FILE_NAMES = {
    "veracode-secrets.json", ".env", ".env.local", "credentials", "credentials.json"
}
MAINFRAME_MARKER_NAMES = {
    "copybook", "copybooks", "cobol", "jcl", "mainframe", "src"
}


def eprint(*args):
    print(*args, file=sys.stderr)


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def is_ignored(path: Path, root: Path) -> bool:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return True
    return any(part.lower() in IGNORED_DIRS for part in rel.parts)


def discover_targets(repo_root: Path):
    found = {}

    for marker, kind in (("pom.xml", "maven"), ("angular.json", "angular"), ("package.json", "node")):
        for p in repo_root.rglob(marker):
            if not p.is_file() or is_ignored(p, repo_root):
                continue
            d = p.parent.resolve()
            existing = found.get(str(d))
            if existing and existing["type"] == "angular":
                continue
            kind2 = "angular" if kind == "node" and (d / "angular.json").exists() else kind
            found[str(d)] = {"path": d, "type": kind2, "marker": marker}

    mainframe_candidates = []
    for p in repo_root.rglob("*"):
        if not p.is_file() or is_ignored(p, repo_root):
            continue
        if p.suffix.lower() in MAINFRAME_SUFFIXES:
            mainframe_candidates.append(p)

    roots = {}
    for p in mainframe_candidates:
        d = p.parent.resolve()
        chosen = d
        current = d
        while current != repo_root and repo_root in current.parents:
            if current.name.lower() in MAINFRAME_MARKER_NAMES:
                chosen = current.parent.resolve()
                break
            try:
                child_names = {x.name.lower() for x in current.iterdir() if x.is_dir()}
            except OSError:
                child_names = set()
            if {"src", "copybook"} <= child_names or {"src", "copybooks"} <= child_names:
                chosen = current.resolve()
                break
            current = current.parent
        roots[str(chosen)] = chosen

    for d in roots.values():
        if str(d) not in found:
            found[str(d)] = {
                "path": d,
                "type": "mainframe",
                "marker": "COBOL/mainframe source"
            }

    return sorted(found.values(), key=lambda x: (str(x["path"]).lower(), x["type"]))

def choose(prompt: str, options):
    print()
    for i, item in enumerate(options, 1):
        print(f"[{i}] {item}")
    while True:
        raw = input(f"{prompt}: ").strip()
        try:
            n = int(raw)
            if 1 <= n <= len(options):
                return n - 1
        except ValueError:
            pass
        print("Invalid selection.")


def run_command(cmd, cwd: Path):
    print("> " + " ".join(str(x) for x in cmd))
    proc = subprocess.run(cmd, cwd=str(cwd))
    if proc.returncode != 0:
        raise RuntimeError(f"Command failed with exit code {proc.returncode}: {' '.join(cmd)}")


def find_maven_executable(target: Path):
    wrapper = target / "mvnw.cmd"
    if wrapper.exists():
        return str(wrapper)
    mvn = shutil.which("mvn")
    if mvn:
        return mvn
    raise RuntimeError("Neither mvnw.cmd nor mvn was found.")


def build_maven_module(target: Path):
    mvn = find_maven_executable(target)
    run_command([mvn, "clean", "package", "-DskipTests"], target)
    artifacts = [
        p for p in (target / "target").glob("*")
        if p.is_file()
        and p.suffix.lower() in {".jar", ".war", ".ear"}
        and not any(x in p.name.lower() for x in ("sources", "javadoc", "original"))
    ]
    if not artifacts:
        raise RuntimeError(f"No JAR/WAR/EAR found under {target / 'target'}")
    return max(artifacts, key=lambda p: p.stat().st_size)


def build_maven_project(target: Path, package_dir: Path):
    """
    Build the selected Maven root and create one ZIP containing application
    JAR/WAR/EAR outputs found beneath the selected project.
    """
    mvn = find_maven_executable(target)
    run_command([mvn, "clean", "package", "-DskipTests"], target)

    artifacts = []
    for p in target.rglob("*"):
        if (
            p.is_file()
            and "target" in p.parts
            and p.suffix.lower() in {".jar", ".war", ".ear"}
            and not any(x in p.name.lower() for x in ("sources", "javadoc", "original"))
        ):
            artifacts.append(p)

    if not artifacts:
        raise RuntimeError(f"No JAR/WAR/EAR artifacts found beneath {target}")

    out = package_dir / f"{target.name}-PROJECT.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in artifacts:
            zf.write(p, p.relative_to(target).as_posix())
    return out


def package_js(target: Path, package_dir: Path, scope: str):
    out = package_dir / f"{target.name}-{scope}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in target.rglob("*"):
            if not p.is_file():
                continue
            rel = p.relative_to(target)
            if any(part in IGNORED_DIRS for part in rel.parts):
                continue
            if p.name in JS_IMPORTANT_FILES or p.suffix.lower() in JS_ALLOWED_SUFFIXES:
                zf.write(p, rel.as_posix())
    return out



def load_secret_file(tool_home: Path):
    secret_path = tool_home / "config" / "veracode-secrets.json"
    if not secret_path.exists():
        raise RuntimeError(
            f"Missing secret file: {secret_path}. "
            "Run create-secret-file.bat, then populate api_id and api_key."
        )

    try:
        data = json.loads(secret_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Unable to parse secret file {secret_path}: {exc}") from exc

    api_id = str(data.get("api_id", "")).strip()
    api_key = str(data.get("api_key", "")).strip()
    api_host = str(data.get("api_host", "")).strip()

    if not api_id or not api_key:
        raise RuntimeError(
            "config/veracode-secrets.json must contain non-empty api_id and api_key."
        )

    return {
        "api_id": api_id,
        "api_key": api_key,
        "api_host": api_host,
    }


def package_mainframe(target: Path, package_dir: Path, scope: str, include_all_files: bool = True):
    """Package the complete selected mainframe module and write an auditable manifest.

    Earlier versions included only a small extension allow-list. That can omit copybooks,
    include members, SQL declarations, extensionless source members, and supporting files
    that are part of the module uploaded through the Veracode UI.
    """
    out = package_dir / f"{target.name}-{scope}-MAINFRAME.zip"
    included = 0
    supported_source_count = 0
    skipped = []
    manifest_files = []

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in sorted(target.rglob("*")):
            if not file_path.is_file():
                continue
            rel = file_path.relative_to(target)
            rel_text = rel.as_posix()
            lower_parts = {part.lower() for part in rel.parts}
            lower_name = file_path.name.lower()

            if lower_parts.intersection({x.lower() for x in IGNORED_DIRS}):
                skipped.append({"path": rel_text, "reason": "ignored directory"})
                continue
            if lower_name in SENSITIVE_FILE_NAMES or "secret" in lower_name or "credential" in lower_name:
                skipped.append({"path": rel_text, "reason": "sensitive filename"})
                continue
            if file_path.suffix.lower() in MAINFRAME_EXCLUDED_SUFFIXES:
                skipped.append({"path": rel_text, "reason": "excluded binary/generated extension"})
                continue

            is_supported_source = file_path.suffix.lower() in MAINFRAME_SUFFIXES or not file_path.suffix
            if not include_all_files and not is_supported_source:
                skipped.append({"path": rel_text, "reason": "not in mainframe source extension list"})
                continue

            zf.write(file_path, rel_text)
            included += 1
            if is_supported_source:
                supported_source_count += 1
            manifest_files.append({
                "path": rel_text,
                "size": file_path.stat().st_size,
                "sha256": sha256_file(file_path),
                "recognized_mainframe_source": is_supported_source,
            })

    if supported_source_count == 0:
        raise RuntimeError(
            f"No supported mainframe source found below {target}. "
            f"Supported extensions include: {', '.join(sorted(MAINFRAME_SUFFIXES))} and extensionless members."
        )

    manifest = {
        "module_root": str(target),
        "archive": str(out),
        "include_all_files": include_all_files,
        "included_file_count": included,
        "recognized_mainframe_source_count": supported_source_count,
        "skipped_file_count": len(skipped),
        "files": manifest_files,
        "skipped": skipped,
    }
    save_json(package_dir / "package-manifest.json", manifest)
    return out, included


def sha256_file(path: Path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def hmac_sha256(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.sha256).digest()


def veracode_auth(api_id: str, api_key_hex: str, host: str, path_query: str, method: str):
    timestamp = str(int(time.time() * 1000))
    nonce = secrets.token_bytes(16)

    try:
        secret = bytes.fromhex(api_key_hex.strip())
    except ValueError as exc:
        raise RuntimeError("VERACODE API secret must be a hexadecimal HMAC key.") from exc

    k_nonce = hmac_sha256(secret, nonce)
    k_date = hmac_sha256(k_nonce, timestamp.encode("utf-8"))
    k_sig = hmac_sha256(k_date, b"vcode_request_version_1")
    canonical = f"id={api_id}&host={host}&url={path_query}&method={method.upper()}".encode("utf-8")
    signature = hmac_sha256(k_sig, canonical).hex()

    return (
        f"VERACODE-HMAC-SHA-256 id={api_id},"
        f"ts={timestamp},nonce={nonce.hex()},sig={signature}"
    )


class VeracodeClient:
    def __init__(self, host: str, api_id: str, api_key: str):
        self.host = host
        self.api_id = api_id
        self.api_key = api_key

    def request_json(self, method: str, path: str, body=None):
        auth = veracode_auth(self.api_id, self.api_key, self.host, path, method)
        headers = {
            "Authorization": auth,
            "Accept": "application/json",
            "User-Agent": "veracode-enterprise-precommit-v4-python/1.0",
        }
        payload = None
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(payload))

        conn = http.client.HTTPSConnection(self.host, timeout=120)
        try:
            conn.request(method, path, body=payload, headers=headers)
            resp = conn.getresponse()
            data = resp.read()
            text = data.decode("utf-8", errors="replace")
            if resp.status < 200 or resp.status >= 300:
                raise RuntimeError(
                    f"Veracode API {method} {path} returned HTTP {resp.status}: {text[:2000]}"
                )
            return json.loads(text) if text.strip() else {}
        finally:
            conn.close()

    def upload_segment(self, path: str, segment_file: Path):
        auth = veracode_auth(self.api_id, self.api_key, self.host, path, "PUT")
        boundary = "---------------------------" + uuid.uuid4().hex
        prefix = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{segment_file.name}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode("utf-8")
        suffix = f"\r\n--{boundary}--\r\n".encode("utf-8")
        file_bytes = segment_file.read_bytes()
        body = prefix + file_bytes + suffix

        headers = {
            "Authorization": auth,
            "Accept": "application/json",
            "User-Agent": "veracode-enterprise-precommit-v4-python/1.0",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
        }

        conn = http.client.HTTPSConnection(self.host, timeout=300)
        try:
            conn.request("PUT", path, body=body, headers=headers)
            resp = conn.getresponse()
            data = resp.read()
            text = data.decode("utf-8", errors="replace")
            if resp.status < 200 or resp.status >= 300:
                raise RuntimeError(
                    f"Veracode segment upload returned HTTP {resp.status}: {text[:2000]}"
                )
            return json.loads(text) if text.strip() else {}
        finally:
            conn.close()


def split_file(input_file: Path, count: int, out_dir: Path):
    if count < 1:
        raise ValueError("Segment count must be >= 1")
    out_dir.mkdir(parents=True, exist_ok=True)
    total = input_file.stat().st_size
    base, rem = divmod(total, count)
    result = []

    with input_file.open("rb") as src:
        for i in range(count):
            size = base + (1 if i < rem else 0)
            out = out_dir / f"segment-{i:04d}.dat"
            with out.open("wb") as dst:
                remaining = size
                while remaining:
                    chunk = src.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    dst.write(chunk)
                    remaining -= len(chunk)
            result.append(out)
    return result


def extract_findings(payload):
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("findings", "results", "flaws"):
        v = payload.get(key)
        if isinstance(v, list):
            return v
    emb = payload.get("_embedded")
    if isinstance(emb, dict):
        for key in ("findings", "results", "flaws"):
            v = emb.get(key)
            if isinstance(v, list):
                return v
    return []


def _next_findings_path(payload):
    """Return a repository-relative API path for the next findings page, if supplied."""
    if not isinstance(payload, dict):
        return None
    links = payload.get("_links") or payload.get("links")
    if isinstance(links, dict):
        nxt = links.get("next")
        if isinstance(nxt, dict):
            nxt = nxt.get("href")
        if isinstance(nxt, str) and nxt.strip():
            parsed = urllib.parse.urlparse(nxt.strip())
            return parsed.path + (("?" + parsed.query) if parsed.query else "")
    return None


def retrieve_all_findings(client, scan_id: str):
    """Retrieve and consolidate every findings page returned by Pipeline Scan."""
    path = f"/pipeline_scan/v1/scans/{scan_id}/findings"
    pages = []
    all_findings = []
    seen_paths = set()

    while path and path not in seen_paths:
        seen_paths.add(path)
        payload = client.request_json("GET", path)
        pages.append(payload)
        all_findings.extend(extract_findings(payload))

        next_path = _next_findings_path(payload)
        if next_path:
            path = next_path
            continue

        page = payload.get("page") if isinstance(payload, dict) else None
        if isinstance(page, dict):
            number = page.get("number")
            total_pages = page.get("total_pages", page.get("totalPages"))
            try:
                number = int(number)
                total_pages = int(total_pages)
            except (TypeError, ValueError):
                break
            if number + 1 < total_pages:
                parsed = urllib.parse.urlparse(path)
                query = urllib.parse.parse_qs(parsed.query)
                query["page"] = [str(number + 1)]
                path = parsed.path + "?" + urllib.parse.urlencode(query, doseq=True)
                continue
        break

    return {
        "scan_id": scan_id,
        "finding_count": len(all_findings),
        "findings": all_findings,
        "pages_retrieved": len(pages),
        "raw_pages": pages,
    }


def pick(d, names, default=""):
    if not isinstance(d, dict):
        return default
    for n in names:
        if n in d and d[n] is not None:
            return d[n]
    return default


def pick_recursive(value, names, default=""):
    """Find a field in Pipeline Scan findings even when Veracode nests issue details."""
    wanted = set(names)
    queue = [value]
    seen = set()
    while queue:
        current = queue.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, dict):
            for name in names:
                if name in current and current[name] is not None:
                    return current[name]
            queue.extend(current.values())
        elif isinstance(current, list):
            queue.extend(current)
    return default


def normalize_severity(value):
    """Return canonical label and numeric severity for Veracode values 0-5 or text labels."""
    labels = {0: "Informational", 1: "Very Low", 2: "Low", 3: "Medium", 4: "High", 5: "Very High"}
    if isinstance(value, dict):
        value = pick(value, ("value", "score", "level", "name", "severity"), "")
    text = str(value).strip()
    try:
        number = int(float(text))
        if number in labels:
            return labels[number], number
    except (TypeError, ValueError):
        pass
    canonical = {
        "veryhigh": "Very High", "very high": "Very High",
        "high": "High", "medium": "Medium", "low": "Low",
        "verylow": "Very Low", "very low": "Very Low",
        "informational": "Informational", "info": "Informational",
    }.get(text.lower().replace("_", " "), text or "Unknown")
    reverse = {v.lower(): k for k, v in labels.items()}
    return canonical, reverse.get(canonical.lower())


def normalize_findings(raw_payload, scope: str, target_name: str, blocking_severities):
    raw = extract_findings(raw_payload)
    normalized = []
    blocking_count = 0

    for f in raw:
        raw_severity = pick_recursive(
            f,
            ("severity", "severity_name", "severityName", "severity_value", "severityValue", "severity_level"),
            "",
        )
        sev, sev_number = normalize_severity(raw_severity)
        is_blocking = sev.lower() in blocking_severities or str(sev_number) in blocking_severities
        if is_blocking:
            blocking_count += 1
        normalized.append({
            "issue_id": pick_recursive(f, ("issue_id", "issueId", "id", "flaw_id", "flawId")),
            "cwe": pick_recursive(f, ("cwe_id", "cweId", "cwe")),
            "severity": sev,
            "severity_number": sev_number,
            "title": pick_recursive(f, ("title", "issue_type", "issueType", "name", "category")),
            "file": pick_recursive(f, ("file", "file_name", "fileName", "filename", "source_file", "sourceFile")),
            "line": pick_recursive(f, ("line", "line_number", "lineNumber")),
            "function": pick_recursive(f, ("function", "procedure", "method")),
            "status": pick_recursive(f, ("status", "finding_status", "findingStatus"), "OPEN"),
            "description": pick_recursive(f, ("description", "issue_details", "issueDetails", "details")),
            "remediation": pick_recursive(f, ("remediation", "recommendation", "fix")),
            "scope": scope,
            "target": target_name,
            "blocking": is_blocking,
            "raw": f,
        })

    return {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "scope": scope,
        "target": target_name,
        "finding_count": len(normalized),
        "blocking_finding_count": blocking_count,
        "findings": normalized,
    }



def save_findings_csv(path: Path, normalized):
    """Write a flat CSV companion to veracode-findings.json for Excel/reporting."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "issue_id", "cwe", "severity", "title", "file", "line", "function",
        "status", "blocking", "scope", "target", "description", "remediation"
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for finding in normalized.get("findings", []):
            row = {}
            for field in fields:
                value = finding.get(field, "")
                if value is None:
                    value = ""
                elif isinstance(value, (dict, list)):
                    value = json.dumps(value, ensure_ascii=False)
                row[field] = value
            writer.writerow(row)


def get_output_target_name(target_path: Path, target_type: str) -> str:
    """Return the project/module name used below .GitHub/output.

    Mainframe scanners often select a physical ``src`` directory. In that
    situation, the output must use its parent module/project name rather than
    producing the incorrect ``output/src/src`` structure.
    """
    path = target_path.resolve()
    if target_type == "mainframe" and path.name.lower() == "src":
        return path.parent.name
    return path.name


def make_output_dir(repo_root: Path, target_name: str, target_type: str):
    """Create the requested repository-root output path.

    All apps: .GitHub/output/veracode/<project-or-module>/<date>
    """
    date = dt.datetime.now().strftime("%Y-%m-%d")
    base = repo_root / ".GitHub" / "output" / "veracode" / target_name / date
    if not (base / "results.json").exists():
        return base
    return base / dt.datetime.now().strftime("%H%M%S")


def install_agent(tool_home: Path, repo_root: Path):
    """Validate the root-level Copilot agent/skill layout.

    The .GitHub directory is intentionally outside veracode-enterprise.
    """
    github_root = repo_root / ".GitHub"
    agent = github_root / "agents" / "veracode-remediation-agent.md"
    skill = github_root / "skills" / "veracode-remediation" / "SKILL.md"
    if not agent.exists():
        raise RuntimeError(f"Missing Copilot agent file: {agent}")
    if not skill.exists():
        raise RuntimeError(f"Missing Copilot remediation skill: {skill}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tool-home", required=True)
    args = parser.parse_args()

    tool_home = Path(args.tool_home.strip().strip('"')).resolve()

    # tool_home may be nested under repository helper directories such as
    # <repo>/.dbFusionGuardAI/.github/veracode-enterprise.  Do not treat those
    # last directories as the repository root.  Prefer the nearest ancestor
    # containing .git; if unavailable, fall back two levels above tool_home.
    repo_root = None
    for candidate in (tool_home, *tool_home.parents):
        if (candidate / ".git").exists():
            repo_root = candidate.resolve()
            break
    if repo_root is None:
        parents = tool_home.parents
        repo_root = (parents[1] if len(parents) > 1 else tool_home.parent).resolve()

    config = load_json(tool_home / "config" / "scanner.json")

    try:
        secret_cfg = load_secret_file(tool_home)
    except Exception as exc:
        eprint(f"ERROR: {exc}")
        return 2

    api_id = secret_cfg["api_id"]
    api_key = secret_cfg["api_key"]
    if secret_cfg.get("api_host"):
        config["api_host"] = secret_cfg["api_host"]

    print("=" * 58)
    print(" Veracode REST Enterprise Pre-Commit Scanner V5 - Mainframe + Secret File")
    print("=" * 58)

    scope_idx = choose("Select scan mode", [
        "PROJECT - build/package selected application/project",
        "MODULE  - build/package selected Maven/Angular/Node module/application",
        "EXISTING VERACODE PACKAGE - submit an existing ZIP/JAR/WAR/EAR unchanged",
    ])

    existing_package = scope_idx == 2
    if existing_package:
        scope = "EXISTING_PACKAGE"
        print()
        raw_package = input("Enter full path to existing Veracode package: ").strip().strip('"')
        artifact = Path(raw_package).expanduser().resolve()
        if not artifact.exists() or not artifact.is_file():
            eprint(f"ERROR: Package file does not exist: {artifact}")
            return 2
        if artifact.suffix.lower() not in {".zip", ".jar", ".war", ".ear"}:
            eprint("ERROR: Existing package must be ZIP, JAR, WAR, or EAR.")
            return 2

        default_name = artifact.stem
        entered_name = input(
            f"Project/report name [{default_name}]: "
        ).strip()
        target_name = entered_name or default_name
        # Make output directory name Windows-safe.
        for ch in '<>:"/\\|?*':
            target_name = target_name.replace(ch, "_")
        target_path = artifact.parent
        target_type = "existing-package"
        output_dir = make_output_dir(repo_root, target_name, target_type)
        package_dir = output_dir / "package"
        package_dir.mkdir(parents=True, exist_ok=True)
        install_agent(tool_home, repo_root)

        # Preserve evidence of exactly which package was submitted without altering it.
        evidence_artifact = package_dir / artifact.name
        try:
            if artifact.resolve() != evidence_artifact.resolve():
                shutil.copy2(artifact, evidence_artifact)
        except OSError:
            shutil.copy2(artifact, evidence_artifact)
    else:
        scope = "PROJECT" if scope_idx == 0 else "MODULE"

        targets = discover_targets(repo_root)
        if not targets:
            eprint(f"ERROR: No Maven/Angular/Node projects found beneath {repo_root}")
            return 2

        labels = [f"{t['path']} ({t['type']})" for t in targets]
        target = targets[choose("Select project/module", labels)]
        target_path: Path = target["path"]
        target_type = target["type"]
        target_name = get_output_target_name(target_path, target_type)

        output_dir = make_output_dir(repo_root, target_name, target_type)
        package_dir = output_dir / "package"
        package_dir.mkdir(parents=True, exist_ok=True)
        install_agent(tool_home, repo_root)

    print()
    print(f"Scope : {scope}")
    print(f"Target: {target_path}")
    print(f"Type  : {target_type}")
    print(f"Output: {output_dir}")
    print()

    try:
        if existing_package:
            artifact = artifact.resolve()
            mainframe_file_count = None
        elif target_type == "maven":
            if scope == "PROJECT":
                artifact = build_maven_project(target_path, package_dir)
            else:
                artifact = build_maven_module(target_path)
            mainframe_file_count = None
        elif target_type == "mainframe":
            artifact, mainframe_file_count = package_mainframe(
                target_path, package_dir, scope,
                bool(config.get("mainframe_include_all_files", True)),
            )
        else:
            artifact = package_js(target_path, package_dir, scope)
            mainframe_file_count = None

        artifact = artifact.resolve()
        size = artifact.stat().st_size
        digest = sha256_file(artifact)

        client = VeracodeClient(
            config.get("api_host", "api.veracode.com"),
            api_id,
            api_key,
        )

        print("Configuring Veracode Pipeline Scan REST request...")
        create_body = {
            "binary_name": artifact.name,
            "binary_size": size,
            "binary_hash": digest,
            "project_name": target_name,
            "dev_stage": config.get("development_stage", "DEVELOPMENT"),
        }
        created = client.request_json("POST", "/pipeline_scan/v1/scans", create_body)
        scan_id = created.get("scan_id")
        segment_count = int(created.get("binary_segments_expected") or 0)

        if not scan_id:
            raise RuntimeError("Veracode response did not contain scan_id.")
        if segment_count < 1:
            raise RuntimeError("Veracode response did not contain a valid binary_segments_expected.")

        seg_dir = package_dir / "segments"
        segments = split_file(artifact, segment_count, seg_dir)
        print(f"Uploading {len(segments)} segment(s)...")
        for i, seg in enumerate(segments):
            print(f"  segment {i + 1}/{len(segments)}")
            client.upload_segment(
                f"/pipeline_scan/v1/scans/{scan_id}/segments/{i}",
                seg,
            )

        print("Starting scan...")
        client.request_json(
            "PUT",
            f"/pipeline_scan/v1/scans/{scan_id}",
            {"scan_status": "STARTED"},
        )

        deadline = time.time() + (int(config.get("max_scan_minutes", 60)) * 60)
        poll_seconds = int(config.get("poll_seconds", 20))
        details = {}

        current_poll = max(5, poll_seconds)
        last_status = None
        while time.time() < deadline:
            details = client.request_json(
                "GET",
                f"/pipeline_scan/v1/scans/{scan_id}",
            )
            status = str(details.get("scan_status", "")).upper()
            if status != last_status:
                print(f"Scan status: {status or 'UNKNOWN'}")
                last_status = status

            if status == "SUCCESS":
                break
            if status in {"ERROR", "FAILED", "CANCELLED", "CANCELED"}:
                raise RuntimeError(
                    f"Veracode scan ended with status {status}: {details.get('message', '')}"
                )
            time.sleep(current_poll)
            current_poll = min(30, current_poll + 5)
        else:
            raise RuntimeError(
                f"Pipeline Scan exceeded configured timeout of "
                f"{config.get('max_scan_minutes', 60)} minutes."
            )

        print("Retrieving complete findings result...")
        findings_payload = retrieve_all_findings(client, scan_id)
        print(
            f"Retrieved {findings_payload.get('finding_count', 0)} finding(s) "
            f"across {findings_payload.get('pages_retrieved', 1)} page(s)."
        )

        save_json(output_dir / "results.json", findings_payload)

        blocking_set = {
            str(x).strip().lower()
            for x in config.get("blocking_severities", ["very high", "high", "5", "4"])
        }
        normalized = normalize_findings(
            findings_payload,
            scope,
            target_name,
            blocking_set,
        )
        save_json(output_dir / "veracode-findings.json", normalized)
        severity_preview = {}
        for item in normalized.get("findings", []):
            key = item.get("severity") or "Unknown"
            severity_preview[key] = severity_preview.get(key, 0) + 1
        print("Severity totals returned by this Pipeline Scan:")
        for key in sorted(severity_preview):
            print(f"  {key}: {severity_preview[key]}")
        # CSV companion for easy Excel/report consumption.
        save_findings_csv(output_dir / "results.csv", normalized)
        save_findings_csv(output_dir / "veracode-findings.csv", normalized)

        metadata = {
            "scan_id": scan_id,
            "scope": scope,
            "target": target_name,
            "target_path": str(target_path),
            "artifact": str(artifact),
            "binary_size": size,
            "binary_hash_sha256": digest,
            "scan_status": details.get("scan_status"),
            "created": created.get("created"),
            "completed": dt.datetime.now(dt.timezone.utc).isoformat(),
            "api_host": config.get("api_host", "api.veracode.com"),
            "input_mode": "EXISTING_VERACODE_PACKAGE" if existing_package else scope,
            "submitted_package_unchanged": bool(existing_package),
            "target_type": target_type,
            "mainframe_packaged_file_count": mainframe_file_count,
            "credentials_source": "config/veracode-secrets.json",
        }
        save_json(output_dir / "scan-metadata.json", metadata)

        gate = "FAIL" if normalized["blocking_finding_count"] else "PASS"

        severity_counts = {}
        for finding in normalized["findings"]:
            key = str(finding.get("severity", "") or "Unknown")
            severity_counts[key] = severity_counts.get(key, 0) + 1

        save_json(output_dir / "scan-status.json", {
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "scope": scope,
            "target": target_name,
            "pipeline_scan_status": "SUCCESS",
            "development_gate": gate,
            "official_veracode_policy_status": "NOT_EVALUATED",
            "finding_count": normalized["finding_count"],
            "blocking_findings": normalized["blocking_finding_count"],
            "severity_counts": severity_counts,
            "findings_file": str(output_dir / "veracode-findings.json"),
            "note": (
                "This is a pre-commit Pipeline Scan development gate. "
                "It is not the official Veracode Platform/UI policy result."
            ),
        })

        # Human-readable summary for developers and audit evidence.
        summary_lines = [
            "Veracode Pre-Commit Development Scan Summary",
            "==========================================",
            "",
            f"Target: {target_name}",
            f"Scope: {scope}",
            "Pipeline Scan Status: SUCCESS",
            f"Development Gate: {gate}",
            "Official Veracode Policy Status: NOT_EVALUATED",
            "",
            f"Total Findings: {normalized['finding_count']}",
            f"Blocking Findings: {normalized['blocking_finding_count']}",
            "",
            "Severity Counts:",
        ]
        for sev in sorted(severity_counts):
            summary_lines.append(f"  {sev}: {severity_counts[sev]}")
        summary_lines += [
            "",
            "Important:",
            "This development scan is intentionally separate from the official",
            "Veracode Platform/UI Upload & Scan policy evaluation.",
            "",
        ]
        (output_dir / "development-scan-summary.txt").write_text(
            "\n".join(summary_lines), encoding="utf-8"
        )

        shutil.rmtree(seg_dir, ignore_errors=True)

        print()
        print("Reports:")
        print(f"  {output_dir}")
        print()
        print("PIPELINE SCAN STATUS: SUCCESS")
        print(f"DEVELOPMENT SECURITY GATE: {gate}")
        print("OFFICIAL VERACODE POLICY STATUS: NOT_EVALUATED")
        print(f"TOTAL FINDINGS: {normalized['finding_count']}")
        print(f"BLOCKING FINDINGS: {normalized['blocking_finding_count']}")
        print()

        if gate == "PASS":
            print("Development gate passed. This does not represent the official UI policy result.")
            return 0

        print("Development gate failed because local blocking severities were found.")
        print("Use Copilot agent: veracode-remediation-agent")
        print(
            f"Prompt: Remediate the latest Veracode findings for "
            f"{target_name} under .GitHub/output/veracode."
        )
        return 1

    except Exception as exc:
        eprint()
        eprint(f"ERROR: {exc}")
        try:
            save_json(output_dir / "execution-error.json", {
                "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
                "scope": scope,
                "target": target_name,
                "error": str(exc),
            })
        except Exception:
            pass
        return 5


if __name__ == "__main__":
    raise SystemExit(main())
