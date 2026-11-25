#!/usr/bin/env python3
"""Generate traceable manifests for models and source repositories.

Facts come from the files/repos themselves. Training provenance the artifacts do not embed
is left as the literal string TO_BE_COMPLETED_BY_USER -- never invented.
Original repositories are only READ (git commands are read-only).
Usage: make_manifests.py --engine-first DIR --engine-post DIR --ctrl-first DIR --ctrl-post DIR [--out manifests]
"""
from __future__ import annotations
import argparse, hashlib, json, subprocess, sys, warnings
from datetime import datetime, timezone
from pathlib import Path

TODO = "TO_BE_COMPLETED_BY_USER"


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed in {repo}: {r.stderr.strip()}")
    return r.stdout


def repo_info(repo: Path, tracked_only_hash_files: bool = True) -> dict:
    head = git(repo, "rev-parse", "HEAD").strip()
    branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
    status = [l for l in git(repo, "status", "--porcelain").splitlines() if l.strip()]
    diff = git(repo, "diff", "HEAD")
    tracked = [f for f in git(repo, "ls-files").splitlines()]
    files = {}
    for f in sorted(tracked):
        p = repo / f
        if p.is_file() and not f.endswith(".joblib"):
            files[f] = sha256_file(p)
    return {
        "path_at_manifest_time": str(repo), "branch": branch, "head_commit": head,
        "head_subject": git(repo, "log", "-1", "--format=%s").strip(),
        "working_tree_clean": not status, "uncommitted_changes": status,
        "uncommitted_diff_sha256": hashlib.sha256(diff.encode()).hexdigest() if diff else None,
        "working_tree_file_sha256": files,   # hashes of the files AS THEY ARE ON DISK (what would be deployed)
        "tracked_file_count": len(tracked),
    }


def model_info(model_path: Path) -> dict:
    info = {"file": str(model_path.name), "sha256": sha256_file(model_path),
            "size_bytes": model_path.stat().st_size}
    try:
        import joblib
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            m = joblib.load(model_path)
        st = m.__getstate__()
        info["introspected"] = {
            "type": type(m).__name__, "n_estimators": getattr(m, "n_estimators", None),
            "classes": [str(c) for c in getattr(m, "classes_", [])],
            "n_features_in": getattr(m, "n_features_in_", None),
            "feature_names_in": [str(x) for x in getattr(m, "feature_names_in_", [])],
            "pickled_with_sklearn_version": st.get("_sklearn_version"),
            "introspection_note": "loaded with the sklearn of the machine that generated this manifest",
        }
    except Exception as e:  # keep going; hash is the essential field
        info["introspected"] = {"error": f"{type(e).__name__}: {e}"}
    info["training_provenance"] = {k: TODO for k in (
        "training_dataset_name", "training_dataset_sha256", "training_script_commit",
        "training_date", "hyperparameters", "feature_set_version", "validation_split",
        "offline_macro_f1", "stun_excluded_in_training_data")}
    return info


def main() -> int:
    ap = argparse.ArgumentParser()
    for a in ("engine-first", "engine-post", "ctrl-first", "ctrl-post"):
        ap.add_argument(f"--{a}", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=Path("manifests"))
    a = ap.parse_args()
    args = vars(a)
    now = datetime.now(timezone.utc).isoformat()
    repos = {"capture_engine_first_n": a.engine_first, "capture_engine_post_n": a.engine_post,
             "python_controller_first_n": a.ctrl_first, "python_controller_post_n": a.ctrl_post}
    for k, p in repos.items():
        if not (p / ".git").exists():
            print(f"ERROR: {k}: {p} is not a git repository", file=sys.stderr); return 2
    sources = {"generated_utc": now, "repos": {k: repo_info(p) for k, p in repos.items()},
               "patch_hashes": {}, "note": "patch_hashes is filled when patches are added to patches/"}
    models = {"generated_utc": now, "models": {
        "first_n": {"used_by_scenarios": ["B"], "controller_repo": "python_controller_first_n",
                    **model_info(a.ctrl_first / "models" / "rf_model.joblib")},
        "post_n": {"used_by_scenarios": ["C", "D"], "controller_repo": "python_controller_post_n",
                   **model_info(a.ctrl_post / "models" / "rf_model.joblib")}}}
    # cross-checks the handoff asked for
    notes = []
    if models["models"]["first_n"]["sha256"] == models["models"]["post_n"]["sha256"]:
        notes.append("ERROR: both models are identical")
    cf, cp = repos["python_controller_first_n"], repos["python_controller_post_n"]
    r = subprocess.run(["diff", "-rq", str(cf), str(cp), "-x", ".venv", "-x", ".git", "-x", "__pycache__",
                        "-x", "*.joblib", "-x", "qos_telemetry_benchmarks.csv"], capture_output=True, text=True)
    models["controller_variants_differ_only_in_model"] = (r.stdout.strip() == "")
    models["controller_diff_excluding_model_and_telemetry_csv"] = r.stdout.strip().splitlines()
    models["notes"] = notes
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "sources.manifest.json").write_text(json.dumps(sources, indent=2) + "\n")
    (a.out / "models.manifest.json").write_text(json.dumps(models, indent=2) + "\n")
    print(f"wrote {a.out}/sources.manifest.json and models.manifest.json")
    return 1 if any(n.startswith("ERROR") for n in notes) else 0


if __name__ == "__main__":
    sys.exit(main())
