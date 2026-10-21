# Distributing FCCSV

The command shown to users is fccsv. The Python import package is still
fccsv3, and the project state directory is still .fccsv3. Keeping those
internal names avoids a state migration during the preview. V2 also installs
a command named fccsv, so install this preview in its own virtual environment
or run a standalone executable. Never keep both packages in one environment
and assume PATH will choose the intended version.

## Python installation for a pilot

From this directory on Windows PowerShell:

    py -3.11 -m venv .venv
    .\.venv\Scripts\python.exe -m pip install -e ".[sheets]"
    .\.venv\Scripts\fccsv.exe --version
    .\.venv\Scripts\fccsv.exe auth status --project <project-directory>

Use this isolated environment for the pilot. Each person needs their own
OAuth token. The project-local .env.local contains only paths to their token
and OAuth client file. Do not distribute a token or the project state database.

## Standalone Windows executable

For a Python-free release, build fccsv.exe on Windows in a clean build
environment with the sheets dependencies and an executable packager such as
PyInstaller. Include Textual if the TUI is part of the release. Keep the
project configuration, CSV files, .env.local, OAuth token, and
.fccsv3/state.sqlite3 outside the executable. A release ZIP can contain the
executable, a quick-start guide, and .env.example; provision the OAuth client
file separately.

No executable has been built or validated in this workspace. Before
distribution, smoke-test the built binary on a clean Windows machine:
version/help, auth login with a personal account, status/diff/plan, and then
push/pull/recover against a disposable spreadsheet. Interrupt a write and
verify recovery. The current fake-Sheet tests are not evidence that Google
write and recovery work live. Do not treat the preview as a production
cutover until that gate passes.

## Table selection

A period selects configured CSVs under the current directory, as in Git.
From the project root it selects all currently configured game-data CSVs.
A mapped file or directory path also works. --all selects every configured
table regardless of cwd. A bare push or pull remains an error.
