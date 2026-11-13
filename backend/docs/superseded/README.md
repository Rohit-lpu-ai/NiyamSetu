# Superseded documents — safe to delete

These two files are **not authoritative**. They were kept only because this
session's tooling was not permitted to delete files; the cleanup that produced
`PROJECT_STRUCTURE_AND_MODULE_GUIDE.md` intended to remove them.

| File | Why it is superseded | Read instead |
|---|---|---|
| `work-package-1-verification.md` | A snapshot of the repository at commit `2cff9a4` / **385 tests**. Every verified claim in it has been re-verified and restated at a later state. Its one unique correction (ambiguous markers 21 → 31) survives in `evaluation-audit.md` (Appendix) and `backend/app/db/README.md` §4. Its venv-drift finding survives in `PROJECT_STRUCTURE_AND_MODULE_GUIDE.md` §8. | `BACKEND_FINAL_COMPLETION_REPORT.md` |
| `backend-finalization-audit.md` | A **work plan** (phases B–G) for the mission that `BACKEND_FINAL_COMPLETION_REPORT.md` reports as completed, plus a snapshot at **516 tests**. Its per-module test inventory survives — refreshed to the real 590-test run — in `PROJECT_STRUCTURE_AND_MODULE_GUIDE.md` §5. Its cosmetic `COMMENT ON COLUMN` defect note survives in `BACKEND_FINAL_COMPLETION_REPORT.md` §10.10. | `BACKEND_FINAL_COMPLETION_REPORT.md`, `final-backend-evaluation.md` |

To finish the cleanup:

```
git rm -r backend/docs/superseded
```
