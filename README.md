# ArenaColours

Recolours Echo VR's arena -- `mpl_arena_a` and `mpl_lobby_b_arena` -- and nothing else.
Echo's blue team becomes one colour and its orange team another, everywhere in those two
maps: walls, geos, glows, lights, reflections, scoreboards, score displays, goals, disc.
Every other map keeps its stock look.

## Use

Run `ArenaColours.exe` (or `ArenaColours.bat` with Python + `app/requirements.txt`).

1. **Folders** -- your `ready-at-dawn-echo-arena` folder (auto-detected), optionally your
   own mod folder (`input-pcvr`) so your other mods are kept, and a work folder with a few GB free.
2. **Colours** -- pick what blue and orange become. Saturation applies to everything;
   brightness scales the arena's baked lighting (1.0 = stock). The strip shows Echo's
   colours (top) and what they become (bottom).
3. **Build & Install** (Echo must be closed). The first install backs up your current
   manifest and mod package to `<work folder>\backup_original`.

**Test load** launches `echovr -spectatorstream` and reports when the arena has loaded and
any engine errors. **Restore stock arena** repacks only your mod folder (or the stock game).

## How it works

| Layer | What changes |
|---|---|
| Textures | colour, glow, rim-light and UI atlases the two maps use, recoloured and made resident |
| Materials | glow / rim / base tint properties |
| UI canvases | element colours of the scoreboards and score displays the maps place |
| Lights | placed lights and light volumes in the two level scenes |
| Reflections | every mip of the reflection cubemaps |
| Lightmap | the arena's baked lighting, recoloured and scaled by *brightness* |
| Runtime | `plugins\ArenaColours.dll` recolours what the game sets while it runs (goals, holo blocks, disc, scoreboard bars) -- only while one of the two maps is loaded |

Echo shares textures, materials and models between maps, so changed ones are cloned under
new names and only the two maps are pointed at the clones. New names are chosen to keep
every hash-sorted engine table in order, and names shared with other resource types, or
used by texture overrides and canvas placements, keep their stock names.

The runtime plugin needs a plugin loader (`dbgcore.dll`) next to `echovr.exe`. It hooks the
final PC build only and does nothing if the game's code differs. **DiscGlow** (optional,
github.com/bollko/Echo-Restoration) adds personal-disc colours and sticky team colours; the
app downloads its installer on request and sets its disc colours to match.

## Build

- `plugin/` -- `build.bat` (MSVC + Detours) -> `out/ArenaColours.dll`, copied to `app/bin/`.
- `evrtool/` -- `go build -o ../app/bin/evrtool.exe .` (Echo package get/list/find/repack/restore over EvrFile).
- `app/` -- PySide6 app; `pyinstaller ArenaColours.spec` for the standalone exe.
