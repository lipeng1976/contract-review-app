# 交付目录树

```
contract-review-app-handoff/
├── .env
├── .env.example
├── .gitignore
├── README.md
├── backend/
│   ├── requirements.txt
│   ├── app/
│   │   ├── __init__.py
│   │   ├── auth.py
│   │   ├── core.py
│   │   ├── db.py
│   │   ├── main.py
│   │   ├── paths.py
│   │   ├── report_pdf.py
│   │   ├── routers/
│   │   │   ├── __init__.py
│   │   │   ├── admin.py
│   │   │   ├── auth.py
│   │   │   ├── contracts.py
│   │   │   ├── system.py
│   │   │   ├── workbench.py
│   ├── db/
│   │   ├── __init__.py
│   │   ├── bootstrap_admin.py
│   │   ├── ingest_report.py
│   │   ├── migrate_relative_paths.py
│   │   ├── purge_contracts.py
│   ├── skills/
│   │   ├── property-contract-review-report/
│   │   │   ├── SKILL.md
│   │   │   ├── references/
│   │   │   │   ├── checklist-catalog.md
│   │   │   │   ├── status-and-format.md
│   │   │   ├── scripts/
│   │   │   │   ├── build_review_report.py
│   │   │   │   ├── check_model.py
│   │   │   │   ├── render_pages.py
│   │   │   │   ├── scan_clauses.py
│   │   │   │   ├── scan_names.py
│   ├── tests/
│   │   ├── e2e_edit.py
│   │   ├── e2e_llm_config.py
│   │   ├── e2e_m1.py
│   │   ├── e2e_m2.py
│   │   ├── e2e_ops.py
│   │   ├── e2e_rbac.py
│   │   ├── e2e_view.py
│   │   ├── e2e_visibility.py
│   │   ├── e2e_workbench.py
│   ├── worker/
│   │   ├── __init__.py
│   │   ├── buyer_tables.py
│   │   ├── extract.py
│   │   ├── llm.py
│   │   ├── parse.py
│   │   ├── render.py
│   │   ├── runner.py
│   │   ├── validate.py
├── db/
│   ├── schema.sql
│   ├── seed.sql
├── docs/
│   ├── API.md
│   ├── ARCHITECTURE.md
│   ├── DATABASE.md
│   ├── FILE_INDEX.md
│   ├── HANDOFF.md
│   ├── OPERATIONS.md
│   ├── TREE.md
│   ├── contract-review-app-design.md
│   ├── 合同审查系统功能说明书.docx
├── frontend/
│   ├── android/
│   │   ├── AndroidManifest.xml
│   │   ├── build.sh
│   │   ├── assets/
│   │   │   ├── backend-url.txt
│   │   ├── java/
│   │   │   ├── cn/
│   │   │   │   ├── contractreview/
│   │   │   │   │   ├── app/
│   │   │   │   │   │   ├── FileProviderLite.java
│   │   │   │   │   │   ├── MainActivity.java
│   │   ├── res/
│   │   │   ├── drawable-nodpi/
│   │   │   │   ├── ic_launcher.png
│   ├── h5/
│   │   ├── index.html
├── logs/
│   ├── api.log
│   ├── supervisor.lock
│   ├── supervisor.log
│   ├── watchdog.lock
│   ├── watchdog.log
│   ├── worker.log
├── ops/
│   ├── contract-watchdog-launch.vbs
│   ├── contract-watchdog-stop.cmd
│   ├── contract-watchdog.cmd
│   ├── status.cmd
│   ├── watchdog-supervisor.mjs
│   ├── watchdog.mjs
```
