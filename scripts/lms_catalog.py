"""Create an inventory from existing downloaded LMS materials."""
import html
import json
from pathlib import Path
from urllib.parse import quote

root = Path("lms_export/materials")
courses = []
md = ["# Содержание материалов", "", "Перечень файлов; содержание самих документов не анализировалось.", ""]
graph = ["flowchart LR", '  root["Материалы LMS"]']
for folder in sorted(root.iterdir()):
    if not folder.is_dir():
        continue
    manifest = folder / "manifest.json"
    records = json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else []
    files = [{"name": p.name, "path": p.relative_to(root).as_posix(), "bytes": p.stat().st_size} for p in sorted(folder.iterdir()) if p.is_file() and p.name not in ("manifest.json", "CONTENTS.md") and not p.name.startswith("page-")]
    errors = [r for r in records if "error" in r]
    title = folder.name
    course_id = title.split(" - ", 1)[0]
    course = {"id": course_id, "title": title, "files": files, "errors": len(errors)}
    courses.append(course)
    lines = ["# " + title, "", "Файлов: " + str(len(files)), ""]
    for item in files:
        lines.append("- [" + item["name"].replace("[", "(").replace("]", ")") + "](" + quote(item["name"]) + ")")
    if errors:
        lines += ["", "Ошибок выгрузки: " + str(len(errors))]
    (folder / "CONTENTS.md").write_text("\n".join(lines), encoding="utf-8")
    md += ["## " + title, "", "Файлов: " + str(len(files)), ""]
    for item in files:
        md.append("- [" + item["name"].replace("[", "(").replace("]", ")") + "](" + quote(item["path"]) + ")")
    label = (title + " · " + str(len(files)) + " файлов").replace('"', "'").replace("[", "(").replace("]", ")")
    graph.append('  root --> c' + course_id + '["' + label + '"]')
    for index, item in enumerate(files):
        label = item["name"].split(" - ", 1)[-1].replace('"', "'").replace("[", "(").replace("]", ")")
        graph.append('  c' + course_id + ' --> f' + course_id + '_' + str(index) + '["' + label + '"]')
(root / "CONTENTS.md").write_text("\n".join(md), encoding="utf-8")
(root / "graph.mmd").write_text("\n".join(graph), encoding="utf-8")
(root / "catalog.json").write_text(json.dumps(courses, ensure_ascii=False, indent=2), encoding="utf-8")
body = ['<!doctype html><meta charset="utf-8"><title>Материалы LMS</title><style>body{font:16px system-ui;max-width:1100px;margin:40px auto;padding:16px}li{margin:8px 0}summary{font-weight:600;padding:14px;cursor:pointer}</style><h1>Содержание материалов LMS</h1>']
for course in courses:
    body.append("<details><summary>" + html.escape(course["title"]) + " — " + str(len(course["files"])) + " файлов</summary><ul>")
    for item in course["files"]:
        body.append('<li><a href="' + quote(item["path"]) + '">' + html.escape(item["name"].split(" - ", 1)[-1]) + "</a></li>")
    body.append("</ul></details>")
(root / "CONTENTS.html").write_text("\n".join(body), encoding="utf-8")
print("CATALOG_SUMMARY=" + json.dumps([{"id": c["id"], "title": c["title"], "files": len(c["files"]), "errors": c["errors"]} for c in courses], ensure_ascii=False))
