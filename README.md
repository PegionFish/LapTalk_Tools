# LapTalk_Tools

In-house tools for video production, telemetry visualization, and rendering workflows.

## Repository Layout

- `csv_visual/` - Windows GUI tool for loading HWiNFO CSV files and exporting timestamp-based line charts as transparent PNG images.
- `frontend2video/` - Electron desktop tool for rendering local HTML pages into video assets, currently targeting transparent `MOV / ProRes 4444` output. See `frontend2video/README.md`.
- `oobe_script/` - Supporting setup scripts kept with the tool repository.
- `plans/` - Planning notes and implementation documents for in-repo tools.

## History Notes

`frontend2video/` was imported from the standalone `frontend2video` repository. Its commit history is now preserved in this monorepo under the `frontend2video/` path.
