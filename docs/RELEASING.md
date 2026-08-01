# Releasing

How a version gets from `master` to a download people can use.

## Why this document exists

**v0.7.3 shipped broken.** Everything had been verified from source, the version
metadata and archive contents were checked, and the release went out — but the
packaged exe was never launched. It carried a first-run bug that left the app
with an empty model picker and a disabled message box, permanently, for anyone
whose first launch happened before they installed a model backend. That is the
whole target audience for a first release.

The lesson is narrow and worth keeping: **an artifact you did not run is an
artifact you did not test.** Testing from source proves the code works. It does
not prove the thing you handed someone works. Step 4 below is the one that
failed, and it is now automated so it cannot be skipped by accident.

## Checklist

### 1. Land the work

Everything ships from `master`, and CI must be green on it. Never tag a branch.

### 2. Bump the version

Two files, kept in sync:

- `server/config.py` → `APP_VERSION`
- `version_info.txt` → `filevers`, `prodvers`, `FileVersion`, `ProductVersion`

Use semver: patch for fixes, minor for features. `build_release.ps1` reads
`APP_VERSION` and names the archive from it, so this drives everything else.

### 3. Update what users read

Skip anything the release doesn't touch, but check each one:

- `README.md` — the feature list, if behaviour changed
- `docs/WHITEPAPER.md` — then regenerate the PDF:
  `.venv\Scripts\python.exe scripts\render_whitepaper.py`
- `packaging\README.txt` — the file that ships *inside* the zip, and the only
  documentation most users will ever open

### 4. Build and verify the artifact

```powershell
.\run_tests.ps1        # backend suites + frontend unit tests, lint, typecheck
.\build_release.ps1    # builds the app, zips it, then verifies the archive
```

`build_release.ps1` calls `verify_release.ps1` automatically. That script
unzips the finished archive somewhere clean, launches the exe, and checks the
server answers, reports the expected version, serves the frontend and its JS
bundle, and — when a local backend is running — that first-run detection
recovers. It refuses to run if port 8040 is already busy, because otherwise it
would test the instance you already have open and pass on someone else's
output.

The build is `--onedir`, so what ships is `SyrudasAI.exe` **plus** the
`_internal\` folder it cannot start without. That is a new way to ship
something broken — an archive holding the exe alone unzips perfectly and dies
on launch — so the layout is checked on both sides: `build_release.ps1` refuses
to package a build with no `_internal`, and `verify_release.ps1` checks the
unzipped archive for it before it launches anything. Neither check is optional,
and neither is a substitute for step 7.

`-SkipVerify` exists for when you genuinely can't free the port. If you use it,
run `.\verify_release.ps1` yourself before shipping.

### 5. Tag, from master

```powershell
git tag -a v1.0.0 -m "v1.0.0: short description"
git push origin v1.0.0
git merge-base --is-ancestor v1.0.0 origin/master   # confirm it is on master
```

### 6. Publish

```powershell
gh release create v1.0.0 "release\SyrudasAI-v1.0.0-win64.zip" `
  --title "Syrudas AI v1.0.0" --notes-file notes.md
```

Write the notes for someone who has never seen the project. Describe what
changed for them, not what changed in the code — and if the release fixes
something that made a previous version unusable, say so in the first line so
anyone who bounced off it knows to come back.

### 7. Check it as a stranger would

Download the zip from the release page **in a browser**, unzip, and run it.
A browser download carries the Mark-of-the-Web that `gh release download` does
not, so this is the only way to see the SmartScreen prompt your users get on an
unsigned exe. It is the last five minutes of the process and the closest thing
to the real thing.

## Known rough edges

- **The exe is unsigned**, so SmartScreen shows "Windows protected your PC" on
  first run. Users have to click *More info → Run anyway*. Fixing this means
  buying a code-signing certificate. That certificate is also the only real fix
  for antivirus false positives: switching to `--onedir` removed the
  self-extraction pattern that most reliably triggers them, but an unsigned
  binary with no download reputation can still be flagged, and has been.
- **The exe needs `_internal\` beside it.** Users who tidy up the folder, or
  who copy just the exe somewhere, get an app that does not start. The end-user
  README says so; it is still the most likely support question.
- **The port is fixed at 8040.** A second copy will attach to the running
  instance instead of starting its own, which can look like the new version
  "did nothing". Worth remembering when testing an upgrade.
