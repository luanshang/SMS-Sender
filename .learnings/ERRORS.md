# Errors

Command failures and integration errors.

---

## [ERR-20260414-001] CDN_DOWNLOAD

**Logged**: 2026-04-14T00:00:00Z
**Priority**: high
**Status**: pending
**Area**: infra

### Summary
Downloading the Vue browser runtime from jsDelivr failed in the development environment.

### Error
```
Authentication failed, see inner exception.
```

### Context
- Attempted to download `vue.esm-browser.prod.js` for local static serving.
- The page currently depends on a browser-side CDN import, which can fail before Vue mounts and leave only the dark background visible.

### Suggested Fix
Use a vendored local Vue runtime in `frontend/`, or switch to a bundled Vite build.

### Metadata
- Reproducible: unknown
- Related Files: frontend/app.js, frontend/index.html
- Tags: vue, cdn, docker, offline-first

---
