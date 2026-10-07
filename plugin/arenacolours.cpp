// ArenaColours: recolours the colours Echo VR sets at RUNTIME (team-coloured goals, holo blocks,
// disc sphere, disc light) with the same hue-band mapping the ArenaColours app bakes into the
// arena's files, so the runtime layer matches the recoloured textures, tints and lights.
//
// Hooks (final PC build, the one live echovr.exe):
//   +0xcc5900  SetInstanceModelColor(component, instance, float rgba[4], u32)
//   +0xbbe870  SetLightColor(component, index, float rgb[3])
// Both are verified by their prologue bytes first; on any mismatch nothing is hooked.
// Hooking is delayed a few seconds so DiscGlow (dinput8.dll, MinHook), if present, hooks first
// and the two chain instead of DiscGlow refusing a function whose bytes it no longer recognises.
//
// ArenaColours.ini next to the DLL, section [ArenaColours], re-read when it changes:
//   Enabled=1
//   BlueTarget=1 0.22 0.72      colour the game's blues/cyans become (linear RGB)
//   OrangeTarget=0.24 1 0.22    colour the game's oranges become
//   Saturation=1.5              1 = unchanged
//   Log=0                       1: write every distinct colour seen to ArenaColours.log

#include <windows.h>
#include <stdint.h>
#include <stdio.h>
#include <math.h>
#include <string>
#include <set>
#include <mutex>
#include <thread>
#include <chrono>
#include <tuple>
#include "detours.h"

namespace {

constexpr uintptr_t SET_COLOUR_RVA = 0xcc5900;
constexpr uint8_t SET_COLOUR_BYTES[] = { 0x40, 0x53, 0x55, 0x56, 0x41, 0x54, 0x41, 0x56, 0x48, 0x83, 0xec, 0x30 };
constexpr uintptr_t SET_LIGHT_RVA = 0xbbe870;
constexpr uint8_t SET_LIGHT_BYTES[] = { 0x40, 0x53, 0x55, 0x56, 0x41, 0x54, 0x41, 0x56, 0x48, 0x83, 0xec, 0x20 };

// Canvas UI colours (scoreboards, score displays, team panels) are set at runtime through these:
//   +0x727020  SetCanvasPrimitiveColor(canvas, primitive id, const float rgb[3])
//   +0x726f40  SetCanvasColor(canvas, const float rgb[3])
// reached from SetColorUINode (+0x37b750) and the R15 scoreboard code.
constexpr uintptr_t UI_PRIM_RVA = 0x727020;
constexpr uint8_t UI_PRIM_BYTES[] = { 0x48, 0x89, 0x5c, 0x24, 0x18, 0x89, 0x54, 0x24, 0x10, 0x57, 0x48, 0x83, 0xec, 0x30 };
constexpr uintptr_t UI_CANVAS_RVA = 0x726f40;
constexpr uint8_t UI_CANVAS_BYTES[] = { 0xf3, 0x0f, 0x10, 0x12, 0xf3, 0x0f, 0x10, 0x81, 0x4c, 0x03, 0x00, 0x00 };
using ui_prim_fn = uint64_t(__fastcall *)(uintptr_t, uint32_t, float *);
using ui_canvas_fn = uint64_t(__fastcall *)(uintptr_t, float *);
ui_prim_fn Real_UiPrim = nullptr;
ui_canvas_fn Real_UiCanvas = nullptr;

// Script-driven light colours (the disc's own light, level light groups):
//   +0x106bd00  SetModelLightColor(component, index, model, const float rgb[3], flags)  <- SetModelLightColorNode
//   +0x106be60  SetLevelLightColor(gamespace, light, const float rgb[3], flags)          <- SetLevelLightColorNode
// Both copy the colour into a deferred-call record before returning, so a stack copy is safe.
constexpr uintptr_t MODEL_LIGHT_RVA = 0x106bd00;
constexpr uint8_t MODEL_LIGHT_BYTES[] = { 0x48, 0x89, 0x5c, 0x24, 0x20, 0x55, 0x57, 0x41, 0x54, 0x41, 0x56, 0x41, 0x57, 0x48, 0x83, 0xec };
constexpr uintptr_t LEVEL_LIGHT_RVA = 0x106be60;
constexpr uint8_t LEVEL_LIGHT_BYTES[] = { 0x40, 0x53, 0x55, 0x56, 0x41, 0x54, 0x41, 0x57, 0x48, 0x83, 0xec, 0x30 };
using model_light_fn = void(__fastcall *)(uintptr_t, uint32_t, uint64_t, float *, uint32_t);
using level_light_fn = void(__fastcall *)(uintptr_t, uint64_t, float *, uint32_t);
model_light_fn Real_ModelLight = nullptr;
level_light_fn Real_LevelLight = nullptr;

// Level gate: recolour only while one of OUR maps is the current map.
//   +0x4fe050  LoadLevel(level*, ...); the level's resource id is the u64 at level+0x58.
// Every mpl_* map load flips the gate (on for ours, off for any other); menus, the global
// levels and sub-levels never change it.
constexpr uintptr_t LOAD_LEVEL_RVA = 0x4fe050;
constexpr uint8_t LOAD_LEVEL_BYTES[] = { 0x48, 0x8b, 0xc4, 0x48, 0x89, 0x58, 0x08, 0x48, 0x89, 0x70, 0x10, 0x48, 0x89, 0x78, 0x18, 0x55 };
constexpr uint64_t OUR_MAPS[] = { 0x576ed3f8428ebc4b /* mpl_arena_a */, 0x6daa00a6d33d44b7 /* mpl_lobby_b_arena */ };
constexpr uint64_t ALL_MAPS[] = { 0x576ed3f8428ebc4b,0xe1a3ae700140d9d5,0x61b0162adbd446ff,0x42670f2bed45703c,0x43e2da7914642604,0xdf5ca7b7dfa383d4,0x836c5b14ccc58201,0xf919f210bbda872c,0x906c4707cbc28c16,0x907f461fccc0961d,0x43e2da7a0c623a19,0xbe0ff249e3c43783,0xbe0ff249e3c43780,0x555be9bcb4759006,0x555be9bcb4759007,0x08a1af9e108def0b,0xd09afd15b1c75c04,0x6daa00a6d33d44b7,0xcb9977f7fc2b4526,0x4325ba19dad081b7,0x3c8d747c21ea8027,0x3c8d747f3ce09d32,0x3c8d747532e18a35,0x3c8d74713ced8c3f,0xa46f74bbc1f3ae6a,0xc3f6cc278ebb6827,0x21f2f1c7f92158b6 };
using load_level_fn = uint64_t(__fastcall *)(uintptr_t, uintptr_t, uint32_t *);
load_level_fn Real_LoadLevel = nullptr;
volatile bool g_in_our_map = false;

using set_colour_fn = void(__fastcall *)(uintptr_t, uint64_t, float *, uint32_t);
using set_light_fn = void(__fastcall *)(uintptr_t, uint64_t, float *);
set_colour_fn Real_SetColour = nullptr;
set_light_fn Real_SetLight = nullptr;

std::string g_dir;
std::mutex g_mutex;
FILE *g_log = nullptr;
std::set<std::tuple<int, uint32_t, uint32_t, uint32_t>> g_seen;

struct Settings {
    bool enabled = true, log = false, arena_only = true;
    float blue_target[3] = { 1.0f, 0.22f, 0.72f };
    float orange_target[3] = { 0.55f, 0.0f, 0.07f };
    float saturation = 1.0f;
} g_s;

// One hue band: hues within `half` of `centre` move fully onto the target hue, fading out over `fade`.
struct Band { float centre, half, fade, target_hue, spread, boost, target_sat, target_val; };
Band g_bands[2];

void Log(const char *fmt, ...) {
    if (!g_log) return;
    va_list ap; va_start(ap, fmt); vfprintf(g_log, fmt, ap); va_end(ap);
    fputc('\n', g_log); fflush(g_log);
}

float HueOf(const float *rgb) {
    float mx = fmaxf(rgb[0], fmaxf(rgb[1], rgb[2])), mn = fminf(rgb[0], fminf(rgb[1], rgb[2])), c = mx - mn;
    if (c <= 0) return 0;
    float h;
    if (mx == rgb[0]) h = fmodf((rgb[1] - rgb[2]) / c, 6.0f);
    else if (mx == rgb[1]) h = (rgb[2] - rgb[0]) / c + 2;
    else h = (rgb[0] - rgb[1]) / c + 4;
    h *= 60.0f;
    return h < 0 ? h + 360.0f : h;
}

float SatOf(const float *rgb) {
    float mx = fmaxf(rgb[0], fmaxf(rgb[1], rgb[2])), mn = fminf(rgb[0], fminf(rgb[1], rgb[2]));
    return mx > 0 ? fmaxf((mx - mn) / mx, 0.05f) : 0.05f;
}

float ValOf(const float *rgb) { return fmaxf(fmaxf(rgb[0], fmaxf(rgb[1], rgb[2])), 0.05f); }

void BuildBands() {
    // Same bands as the app's file pass: blue/cyan around 212, orange (incl. Echo's red-orange) around 25.
    g_bands[0] = { 212.0f, 35.0f, 8.0f, HueOf(g_s.blue_target), 0.25f, 0.40f, SatOf(g_s.blue_target), ValOf(g_s.blue_target) };
    g_bands[1] = { 25.0f, 17.0f, 6.0f, HueOf(g_s.orange_target), 0.30f, 0.25f, SatOf(g_s.orange_target), ValOf(g_s.orange_target) };
}

// rgb (any range) -> recoloured, in place. Value is kept (boosted inside a band); returns true if changed.
bool Recolour(float *rgb, bool ldr = false) {
    float r = fmaxf(rgb[0], 0), g = fmaxf(rgb[1], 0), b = fmaxf(rgb[2], 0);
    float mx = fmaxf(r, fmaxf(g, b)), mn = fminf(r, fminf(g, b)), c = mx - mn;
    if (mx <= 0 || c <= 0) return false;
    float in[3] = { r, g, b };
    float h = HueOf(in), s = c / mx, v = mx, h0 = h, wsum = 0, cap = 1.0f;
    for (const Band &band : g_bands) {
        float dh = fmodf(h0 - band.centre + 540.0f, 360.0f) - 180.0f;
        float w = fminf(fmaxf((band.half + band.fade - fabsf(dh)) / band.fade, 0.0f), 1.0f) * fminf(s / 0.15f, 1.0f);
        if (w <= 0) continue;
        float tgt = fmodf(band.target_hue + dh * band.spread + 360.0f, 360.0f);
        float diff = fmodf(tgt - h + 540.0f, 360.0f) - 180.0f;
        h = fmodf(h + w * diff + 360.0f, 360.0f);
        v *= 1.0f + w * (band.target_val * (1.0f + band.boost) - 1.0f);
        cap = fminf(cap, band.target_sat);
        wsum += w;
    }
    if (wsum <= 0 && g_s.saturation == 1.0f) return false;
    if (ldr) v = fminf(v, 1.0f);
    s = fminf(s * g_s.saturation, 1.0f);
    float w = fminf(wsum, 1.0f);
    s = s + w * (fminf(s, cap) - s);
    float hp = h / 60.0f, x = 1 - fabsf(fmodf(hp, 2.0f) - 1);
    float o[3];
    switch (((int)floorf(hp)) % 6) {
        case 0: o[0] = 1; o[1] = x; o[2] = 0; break;
        case 1: o[0] = x; o[1] = 1; o[2] = 0; break;
        case 2: o[0] = 0; o[1] = 1; o[2] = x; break;
        case 3: o[0] = 0; o[1] = x; o[2] = 1; break;
        case 4: o[0] = x; o[1] = 0; o[2] = 1; break;
        default: o[0] = 1; o[1] = 0; o[2] = x; break;
    }
    float chroma = v * s, off = v - chroma;
    for (int i = 0; i < 3; ++i) rgb[i] = o[i] * chroma + off;
    return true;
}

uint32_t Bits(float f) { uint32_t u; memcpy(&u, &f, 4); return u; }

void Note(int kind, const float *before, const float *after) {
    if (!g_s.log || !g_log) return;
    if (g_seen.emplace(kind, Bits(before[0]), Bits(before[1]), Bits(before[2])).second)
        Log("%s %.4f %.4f %.4f -> %.4f %.4f %.4f", kind == 2 ? "ui" : kind ? "light" : "model",
            before[0], before[1], before[2], after[0], after[1], after[2]);
}

void __fastcall Hooked_SetColour(uintptr_t component, uint64_t instance, float *rgba, uint32_t extra) {
    float out[4];
    bool changed = false;
    if (rgba) {
        std::lock_guard<std::mutex> lock(g_mutex);
        if (g_s.enabled && (g_in_our_map || !g_s.arena_only)) {
            memcpy(out, rgba, sizeof(out));
            changed = Recolour(out);
            Note(0, rgba, changed ? out : rgba);
        }
    }
    Real_SetColour(component, instance, changed ? out : rgba, extra);
}

void __fastcall Hooked_SetLight(uintptr_t component, uint64_t index, float *rgb) {
    float out[3];
    bool changed = false;
    if (rgb) {
        std::lock_guard<std::mutex> lock(g_mutex);
        if (g_s.enabled && (g_in_our_map || !g_s.arena_only)) {
            memcpy(out, rgb, sizeof(out));
            changed = Recolour(out);
            Note(1, rgb, changed ? out : rgb);
        }
    }
    Real_SetLight(component, index, changed ? out : rgb);
}

uint64_t __fastcall Hooked_UiPrim(uintptr_t canvas, uint32_t id, float *rgb) {
    float out[3];
    bool changed = false;
    if (rgb) {
        std::lock_guard<std::mutex> lock(g_mutex);
        if (g_s.enabled && (g_in_our_map || !g_s.arena_only)) {
            memcpy(out, rgb, sizeof(out));
            changed = Recolour(out, true);
            Note(2, rgb, changed ? out : rgb);
        }
    }
    return Real_UiPrim(canvas, id, changed ? out : rgb);
}

uint64_t __fastcall Hooked_UiCanvas(uintptr_t canvas, float *rgb) {
    float out[3];
    bool changed = false;
    if (rgb) {
        std::lock_guard<std::mutex> lock(g_mutex);
        if (g_s.enabled && (g_in_our_map || !g_s.arena_only)) {
            memcpy(out, rgb, sizeof(out));
            changed = Recolour(out, true);
            Note(2, rgb, changed ? out : rgb);
        }
    }
    return Real_UiCanvas(canvas, changed ? out : rgb);
}

void __fastcall Hooked_ModelLight(uintptr_t cs, uint32_t index, uint64_t model, float *rgb, uint32_t flags) {
    float out[3];
    bool changed = false;
    if (rgb) {
        std::lock_guard<std::mutex> lock(g_mutex);
        if (g_s.enabled && (g_in_our_map || !g_s.arena_only)) {
            memcpy(out, rgb, sizeof(out));
            changed = Recolour(out);
            Note(1, rgb, changed ? out : rgb);
        }
    }
    Real_ModelLight(cs, index, model, changed ? out : rgb, flags);
}

void __fastcall Hooked_LevelLight(uintptr_t gs, uint64_t light, float *rgb, uint32_t flags) {
    float out[3];
    bool changed = false;
    if (rgb) {
        std::lock_guard<std::mutex> lock(g_mutex);
        if (g_s.enabled && (g_in_our_map || !g_s.arena_only)) {
            memcpy(out, rgb, sizeof(out));
            changed = Recolour(out);
            Note(1, rgb, changed ? out : rgb);
        }
    }
    Real_LevelLight(gs, light, changed ? out : rgb, flags);
}

uint64_t ReadLevelId(uintptr_t level) {
    __try { return *reinterpret_cast<uint64_t *>(level + 0x58); }
    __except (EXCEPTION_EXECUTE_HANDLER) { return 0; }
}

uint64_t __fastcall Hooked_LoadLevel(uintptr_t level, uintptr_t p2, uint32_t *p3) {
    uint64_t id = ReadLevelId(level);
    bool is_map = false, ours = false;
    for (uint64_t m : ALL_MAPS) is_map |= (m == id);
    for (uint64_t m : OUR_MAPS) ours |= (m == id);
    if (is_map) g_in_our_map = ours;
    if (g_log) {
        std::lock_guard<std::mutex> lock(g_mutex);
        Log("LoadLevel %016llx%s -> recolour %s", (unsigned long long)id, is_map ? " (map)" : "", g_in_our_map ? "ON" : "off");
    }
    return Real_LoadLevel(level, p2, p3);
}

void ReadRGB(const std::string &ini, const char *key, float *out) {
    char buf[128];
    GetPrivateProfileStringA("ArenaColours", key, "", buf, sizeof(buf), ini.c_str());
    float v[3];
    if (sscanf_s(buf, "%f %f %f", &v[0], &v[1], &v[2]) == 3) memcpy(out, v, sizeof(v));
}

void LoadSettings() {
    std::string ini = g_dir + "ArenaColours.ini";
    std::lock_guard<std::mutex> lock(g_mutex);
    g_s.enabled = GetPrivateProfileIntA("ArenaColours", "Enabled", 1, ini.c_str()) != 0;
    g_s.log = GetPrivateProfileIntA("ArenaColours", "Log", 0, ini.c_str()) != 0;
    g_s.arena_only = GetPrivateProfileIntA("ArenaColours", "ArenaOnly", 1, ini.c_str()) != 0;
    ReadRGB(ini, "BlueTarget", g_s.blue_target);
    ReadRGB(ini, "OrangeTarget", g_s.orange_target);
    char buf[64];
    GetPrivateProfileStringA("ArenaColours", "Saturation", "1.0", buf, sizeof(buf), ini.c_str());
    g_s.saturation = (float)atof(buf);
    BuildBands();
    if (g_s.log && !g_log) {
        std::string lp = g_dir + "ArenaColours.log";
        g_log = _fsopen(lp.c_str(), "w", _SH_DENYWR);
    }
}

bool Matches(uintptr_t addr, const uint8_t *bytes, size_t n) {
    __try { return memcmp(reinterpret_cast<void *>(addr), bytes, n) == 0; }
    __except (EXCEPTION_EXECUTE_HANDLER) { return false; }
}

// A function DiscGlow (or anything else) already patched starts with a jmp: chain onto it.
bool PatchedOrMatches(uintptr_t addr, const uint8_t *bytes, size_t n) {
    if (Matches(addr, bytes, n)) return true;
    uint8_t first = 0;
    __try { first = *reinterpret_cast<uint8_t *>(addr); } __except (EXCEPTION_EXECUTE_HANDLER) { return false; }
    return first == 0xE9 || first == 0xFF;
}

void InstallThread() {
    std::this_thread::sleep_for(std::chrono::seconds(6));
    uintptr_t base = reinterpret_cast<uintptr_t>(GetModuleHandleA(nullptr));
    uintptr_t colour = base + SET_COLOUR_RVA, light = base + SET_LIGHT_RVA;
    if (!PatchedOrMatches(colour, SET_COLOUR_BYTES, sizeof(SET_COLOUR_BYTES)) ||
        !PatchedOrMatches(light, SET_LIGHT_BYTES, sizeof(SET_LIGHT_BYTES))) {
        Log("echovr.exe is not the expected build -- nothing hooked");
        return;
    }
    uintptr_t uiprim = base + UI_PRIM_RVA, uicanvas = base + UI_CANVAS_RVA;
    bool ui_ok = PatchedOrMatches(uiprim, UI_PRIM_BYTES, sizeof(UI_PRIM_BYTES)) &&
                 PatchedOrMatches(uicanvas, UI_CANVAS_BYTES, sizeof(UI_CANVAS_BYTES));
    Real_UiPrim = reinterpret_cast<ui_prim_fn>(uiprim);
    Real_UiCanvas = reinterpret_cast<ui_canvas_fn>(uicanvas);
    uintptr_t mlight = base + MODEL_LIGHT_RVA, llight = base + LEVEL_LIGHT_RVA;
    bool lights_ok = PatchedOrMatches(mlight, MODEL_LIGHT_BYTES, sizeof(MODEL_LIGHT_BYTES)) &&
                     PatchedOrMatches(llight, LEVEL_LIGHT_BYTES, sizeof(LEVEL_LIGHT_BYTES));
    Real_ModelLight = reinterpret_cast<model_light_fn>(mlight);
    Real_LevelLight = reinterpret_cast<level_light_fn>(llight);
    uintptr_t loadlevel = base + LOAD_LEVEL_RVA;
    bool gate_ok = PatchedOrMatches(loadlevel, LOAD_LEVEL_BYTES, sizeof(LOAD_LEVEL_BYTES));
    Real_LoadLevel = reinterpret_cast<load_level_fn>(loadlevel);
    Real_SetColour = reinterpret_cast<set_colour_fn>(colour);
    Real_SetLight = reinterpret_cast<set_light_fn>(light);
    DetourTransactionBegin();
    DetourUpdateThread(GetCurrentThread());
    DetourAttach(&(PVOID &)Real_SetColour, Hooked_SetColour);
    DetourAttach(&(PVOID &)Real_SetLight, Hooked_SetLight);
    if (gate_ok) DetourAttach(&(PVOID &)Real_LoadLevel, Hooked_LoadLevel);
    if (lights_ok) {
        DetourAttach(&(PVOID &)Real_ModelLight, Hooked_ModelLight);
        DetourAttach(&(PVOID &)Real_LevelLight, Hooked_LevelLight);
    }
    if (ui_ok) {
        DetourAttach(&(PVOID &)Real_UiPrim, Hooked_UiPrim);
        DetourAttach(&(PVOID &)Real_UiCanvas, Hooked_UiCanvas);
    }
    LONG err = DetourTransactionCommit();
    if (err == NO_ERROR) Log("hooked SetInstanceModelColor + SetLightColor%s%s%s", gate_ok ? " + level gate" : " (level gate skipped)", lights_ok ? " + model/level light colours" : " (script lights skipped)", ui_ok ? " + canvas UI colours" : " (canvas UI skipped: bytes differ)");
    else Log("hook failed: %ld", err);
}

void WatchThread() {
    std::string ini = g_dir + "ArenaColours.ini";
    FILETIME last = {};
    for (;;) {
        WIN32_FILE_ATTRIBUTE_DATA a;
        if (GetFileAttributesExA(ini.c_str(), GetFileExInfoStandard, &a) && CompareFileTime(&a.ftLastWriteTime, &last) != 0) {
            last = a.ftLastWriteTime;
            LoadSettings();
        }
        std::this_thread::sleep_for(std::chrono::seconds(1));
    }
}

} // namespace

BOOL APIENTRY DllMain(HMODULE self, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(self);
        char path[MAX_PATH] = {};
        GetModuleFileNameA(self, path, MAX_PATH);
        g_dir = path;
        g_dir = g_dir.substr(0, g_dir.find_last_of("\\/") + 1);
        LoadSettings();
        std::thread(InstallThread).detach();
        std::thread(WatchThread).detach();
    }
    return TRUE;
}
