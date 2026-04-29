# React Front-End Migration

This folder is the new componentized front-end for V-OS.

## Goals

- Keep the current FastAPI API surface unchanged.
- Replace `index.html`, `login.html`, and `admin.html` progressively instead of in one cut.

## Local Commands

1. Install Node.js 18+.
2. Run `npm install`.
3. Use `npm run dev` during migration.
4. Use `npm run build` to emit `frontend/dist`.

Once `frontend/dist/index.html` exists, FastAPI will automatically prefer the React bundle for:

- `/`
- `/login`
- `/account`
- `/admin`

## Current Scope

- Public creator landing and submission shell
- Login shell
- Creator account dashboard shell
- Admin overview shell

This scaffold is wired to the existing backend endpoints, but it has not been build-tested on this machine because Node/npm were not installed in the environment at edit time.
