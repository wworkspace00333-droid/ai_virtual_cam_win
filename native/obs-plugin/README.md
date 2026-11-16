# vcstudio-obs — OBS Studio plugin (open source, GPL-2.0-or-later)

Shares OBS's final program output with AI Virtual Camera Studio through shared memory (BGRA, triple buffered,
per-slot sequence lock), so the app gets OBS video without OBS Virtual Camera.

## Licence

| File | Licence |
|---|---|
| `vcstudio-obs.c` | GPL-2.0-or-later (`COPYING`), like OBS Studio, which the plugin links against (`obs.dll`, `obs-frontend-api.dll`) |
| `vcs_frame_shm.h` | MIT — the shared-memory protocol, also used by the proprietary program |
| `scripts/build_obs_plugin_win.ps1`, `scripts/build_obs_plugin.sh` | GPL-2.0-or-later (build scripts of the plugin) |

AI Virtual Camera Studio itself is proprietary and is a separate program: it does not link the plugin or OBS, it
only reads frames from the shared memory the plugin writes.

## Source with every copy

The Windows installer ships this folder's source next to the plugin binary, in
`<install folder>\obs-plugin-source\` (the plugin, the protocol header, the build scripts, `COPYING`). That is the
complete corresponding source of `vcstudio-obs.dll`: it is built with MSVC from these files and the OBS Studio
headers of the matching OBS version (https://github.com/obsproject/obs-studio), as described in the build script.

## Build

- Windows: `scripts\build_obs_plugin_win.ps1 -Headers <obs-studio source tree of the installed OBS version>`
- macOS: `scripts/build_obs_plugin.sh`
