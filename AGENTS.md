# Factory Monitor Blueprint

This repository is an architecture, execution, and verification handoff.

- Keep `implementation/` byte-identical to the source snapshot in `evidence/implementation-manifest.json`; changes require an explicit new snapshot, source review, and refreshed evidence.
- Root docs are the current runbooks. Historical implementation docs retain their original scope and dates.
- Never commit production images, customer identities, credentials, local configuration, models, installers, wheels, virtual environments, or private logs. Only explicitly synthetic or sanitized evidence may be included.
- User-authorized full model/installer/wheel resources belong in private Release attachments, with pinned manifests and fresh download readback; keep them out of Git history.
- Screen capture, native UI control, behavioral accuracy, and field acceptance require separate target-device evidence. Synthetic tests cannot satisfy them.
- Do not enable production clicking or upload camera images. Model/cloud analysis remains local by default.
- Preserve mainland-China offline operation, RTX 4060 baseline as an unverified design constraint, existing Windows fixes, and the original 180-second/hour overview budget.
- Documentation commands must use actual checked-in scripts and clearly state prerequisites, side effects, expected outputs, failure paths, and rollback.
- One independent final review. Workers only edit assigned files and never commit other workers changes.
