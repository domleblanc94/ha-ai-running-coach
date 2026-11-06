"""Build an allowlisted public source tree; never copy personal runtime state."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

SOURCE_FILES=["source_policy.py","import_activities.py","ai_coach.py","planning_engine.py","training_calendar.py","model_provider.py","migrate_plan.py","database_manager.py","garmin_sync.py","garmin_personal_api.py","workout_schema.py","parse_coach_output.py","cron_daily_update.py","generate_dashboard.py","dashboard_template.py","dashboard_panels.py","publish_if_ready.py","health_status.py","coach_checkin.py","coaching_eval.py","eval_cases.json","setup_coach.py","run_offline_tests.py","README.md","requirements.txt","pyproject.toml",".env.example",".gitignore","prepare_release.py"]

def build_release(source,output,create_archive=True):
    source=Path(source).resolve();output=Path(output).resolve()
    if output==source or source in output.parents and output.relative_to(source).parts[0] not in {"release","dist"}:
        raise ValueError("Use a separate output directory, or release/ inside this checkout")
    archive=output.parent/(output.name+".zip")
    if create_archive and archive.exists():raise ValueError("Archive already exists; choose a new output name")
    output.mkdir(parents=True,exist_ok=True)
    if any(output.iterdir()):raise ValueError("Release output must be empty; choose a new directory")
    paths=list(SOURCE_FILES)
    paths.extend("dashboard_assets/"+name for name in ["chart-LICENSE.md","chart.js","fa-brands-400.woff2","fa-regular-400.woff2","fa-solid-900.woff2","fontawesome-LICENSE.txt","fontawesome.css","marked-LICENSE.md","marked.js","purify-LICENSE.txt","purify.js","sources.json","tailwind-LICENSE.txt","tailwind.js","workout-fit.js","workout-ui.js"])
    paths.extend(["examples/athlete.template.md","examples/activities.csv","examples/activities.json","docs/THIRD_PARTY.md","docs/RELEASE_REVIEW.md",".github/workflows/offline-tests.yml"])
    paths.extend(str(p.relative_to(source)).replace("\\","/") for p in (source/"tests").glob("test_*.py") if p.is_file())
    if (source/"LICENSE").is_file():paths.append("LICENSE")
    for name in sorted(set(paths)):
        origin=source/name
        if origin.is_symlink() or source not in origin.resolve().parents:
            raise ValueError("Release sources must be regular files within the checkout: "+name)
        if not origin.is_file():raise ValueError("Required release source missing: "+name)
        target=output/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(origin,target)
    manifest={"format":1,"release":"0.2.3-beta.4","license_status":"selected" if (output/"LICENSE").exists() else "pending maintainer decision","files":[{"path":name,"sha256":hashlib.sha256((output/name).read_bytes()).hexdigest()} for name in sorted(set(paths))]}
    (output/"release-manifest.json").write_text(json.dumps(manifest,indent=2)+"\n",encoding="utf-8")
    if not create_archive:return output
    with zipfile.ZipFile(archive,"w",zipfile.ZIP_DEFLATED) as z:
        for name in sorted(set(paths))+["release-manifest.json"]:z.write(output/name,output.name+"/"+name)
    return archive

if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--output",type=Path,required=True);a=p.parse_args()
    print(build_release(Path(__file__).resolve().parent,a.output))
