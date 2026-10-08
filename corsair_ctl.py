#!/usr/bin/env python3
"""Corsair K60 lighting control through the local OpenLinkHub HTTP API.

OpenLinkHub already owns the keyboard over hidraw (it holds the RGB engine and
the FN keys), so this helper talks to it on 127.0.0.1:27003 instead of opening
the device itself. Two consequences worth knowing:

* No root, no udev rule, no second writer fighting for the same hidraw node.
* The colour cannot be read back from the hardware, so `status` reports
  OpenLinkHub's own record of the last applied profile plus whatever this panel
  wrote last (state file). The lamp itself is only witnessed by an eye.

Commands (all print one JSON object on stdout):
  status                  device + profiles + last known colour/brightness
  color <#rrggbb>         static colour on this keyboard
  brightness <0-3>        hardware brightness level
  effect <profileId>      switch RGB profile (any id from `status.profiles`)
  off                      same as `effect off`
  theme                    follow the Omarchy theme's keyboard.rgb (and apply it now)
  theme off                stop following the theme

Theming: Omarchy lets a theme ship `keyboard.rgb` next to its `colors.toml` — one
RRGGBB colour, optionally with a leading `#` (see Omarchy's own theming notes; the
stock tokyo-night theme ships `ff00ff`). With `theme` on, the panel re-applies that
colour whenever the theme file changes.
"""

import sys
import tempfile
import tomllib

sys.dont_write_bytecode = True  # a .pyc here makes the shell reload the widget

import contextlib
import io
import json
import os
import re
import time
import urllib.error
import urllib.request

BASE = os.environ.get("CORSAIR_API", "http://127.0.0.1:27003")
PRODUCT = os.environ.get("CORSAIR_PRODUCT", "K60")
STATE_PATH = os.path.join(
    os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")),
    "omarchy", "corsair", "state.json",
)
# Profile ids the OpenLinkHub UI hides from a keyboard's RGB list: they belong
# to other device kinds (or are the UI's own grouping).
NOT_A_KEYBOARD_EFFECT = {"keyboard", "mouse", "stand", "mousepad", "headset", "custom"}


def http(path, payload=None, method="GET"):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=6) as resp:
        return resp.read().decode("utf-8", "replace")


LAST_ACTION = os.path.join(os.path.dirname(STATE_PATH), "last-action.json")
# Omarchy keeps the active theme here; a theme may ship keyboard.rgb beside its
# colors.toml, and Omarchy's own tokyo-night ships one (`ff00ff`).
THEME_RGB = os.path.join(
    os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")),
    "omarchy", "current", "theme", "keyboard.rgb",
)
THEME_COLORS = os.path.join(os.path.dirname(THEME_RGB), "colors.toml")


def record_action(cmd, args):
    """One line of trace for the panel's own clicks. The widget runs this file
    detached, so a press that never reaches here, and one the API refuses, look
    identical from the outside — this is the only witness."""
    try:
        with open(LAST_ACTION, "w") as fh:
            json.dump({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "cmd": cmd, "args": list(args)}, fh)
    except OSError:
        pass


def theme_color(path=None):
    """The active theme's keyboard colour, or None when it ships none.

    Omarchy's convention is one RRGGBB colour in `keyboard.rgb`, optionally with
    a leading `#` (its own tokyo-night writes `ff00ff`, this machine's theme
    writes `#e4c124`). Read bounded and matched whole, so a hand-edited file
    cannot smuggle anything in: a short read returns None rather than a guess.
    """
    try:
        with open(path or THEME_RGB) as fh:
            raw = fh.read(32)
    except OSError:
        return None
    match = re.fullmatch(r"#?([0-9a-fA-F]{6})", raw.strip())
    return "#" + match.group(1).lower() if match else None


def theme_palette(path=None):
    """The theme's colours, accent first, for effects that need more than one.

    Omarchy themes are TOML (`colors.toml`); `accent` is the colour a theme is
    known by, `selection` and `foreground` give a second one to run a gradient
    to. Unreadable file, bad TOML or a malformed colour means an empty list —
    the caller then keeps whatever it had rather than inventing a colour.
    """
    try:
        with open(path or THEME_COLORS, "rb") as fh:
            doc = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return []
    out = []
    for key in ("accent", "selection", "foreground"):
        value = str(doc.get(key, "")).strip()
        if re.fullmatch(r"#[0-9a-fA-F]{6}", value) and value.lower() not in out:
            out.append(value.lower())
    return out


def cmd_theme(serial, args):
    """Follow the theme's colour. `theme off` stops following."""
    if args and args[0].lower() in ("off", "av"):
        write_state(follow_theme=False)
        return
    hexcolor = theme_color()
    if not hexcolor:
        fail("temat har ingen keyboard.rgb (inget att följa)")
    set_color(serial, hexcolor)
    write_state(follow_theme=True, theme_color=hexcolor)


def read_state():
    try:
        with open(STATE_PATH) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def write_state(**updates):
    state = read_state()
    state.update(updates)
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w") as fh:
        json.dump(state, fh)


def check(body, what):
    """The API answers 200 + status 1 on success, and 200 + status 0 with a
    message when it refused ("Invalid speed"). A refusal that is swallowed
    looks exactly like a lamp that ignores you."""
    try:
        doc = json.loads(body, strict=False)
    except ValueError:
        return
    if doc.get("status") != 1:
        raise RuntimeError(doc.get("message") or "%s rejected" % what)


def find_serial():
    """Serial of the first visible device whose product name matches PRODUCT."""
    doc = json.loads(http("/api/devices/"))
    for serial, info in doc.get("devices", {}).items():
        if info.get("Hidden") or serial == "cluster":
            continue
        if PRODUCT.lower() in str(info.get("Product", "")).lower():
            return serial, info.get("Product", PRODUCT)
    return None, PRODUCT


def fail(message):
    print(json.dumps({"ok": False, "error": message}))
    raise SystemExit(0)


def hex_to_rgb(value):
    value = value.lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    if not re.fullmatch(r"[0-9a-fA-F]{6}", value):
        fail("colour must be #rrggbb")
    return [int(value[i:i + 2], 16) for i in (0, 2, 4)]


def rgb_to_hex(color):
    return "#%02x%02x%02x" % (color.get("red", 0), color.get("green", 0), color.get("blue", 0))


def color_node(rgb, brightness=1):
    return {"red": rgb[0], "green": rgb[1], "blue": rgb[2], "brightness": brightness}


def clamp_speed(speed):
    """The API rejects speed 0 ("Invalid speed"); its own slider runs 1..10."""
    try:
        return max(1, min(10, int(float(speed))))
    except (TypeError, ValueError):
        return 1


def activate(profile):
    """Switch the profile that OpenLinkHub RENDERS for this device.

    Measured: the per-device PUT /api/color/change updates the profile
    *definition* and answers success, but the engine keeps sending the old
    effect (frames captured off /dev/hidraw6 stayed animated through it). The
    global route is what the engine listens to — with it, a static profile
    stopped the frame stream and a rainbow profile started it again.
    """
    body = http("/api/color/global", {"profile": profile}, method="POST")
    check(body, "activate")


def persist(serial):
    """OpenLinkHub keeps an applied change in RAM only. Its profile loop reloads
    from disk and overwrites it within ~30 s (measured: a green static colour
    reverted to the stored orange after ~10 s and stayed green 70 s+ once this
    call had run). This is the app's own "Save profile" button, so the change
    survives the reload instead of flickering back."""
    body = http(
        "/api/keyboard/profile/save",
        {"deviceId": serial, "keyboardProfileName": "0", "new": False},
        method="POST",
    )
    check(body, "save")


def set_color(serial, hexcolor):
    rgb = hex_to_rgb(hexcolor)
    # profile "static" is the plain-colour slot; every colour family is applied
    # through the same PUT, so switching effect and setting a colour is one call.
    payload = {
        "deviceId": serial,
        "profile": "static",
        "startColor": color_node(rgb),
        "endColor": color_node(rgb),
        "middleColor": color_node([0, 0, 0], 0),
        "speed": 1,
        "alternateColors": False,
        "rgbDirection": 0,
    }
    body = http("/api/color/change", payload, method="PUT")
    check(body, "colour")
    activate("static")
    persist(serial)
    write_state(color=rgb_to_hex(color_node(rgb)), effect="static")
    return body


def set_effect(serial, profile):
    """Re-apply a profile with the values OpenLinkHub already stores for it."""
    doc = json.loads(http("/api/color/profile/%s/%s" % (serial, profile)))
    current = doc.get("data")
    if not current:
        raise RuntimeError(doc.get("message") or ("unknown effect: %s" % profile))
    body = http("/api/color/change", payload_from(serial, profile, current), method="PUT")
    check(body, "effect")
    activate(profile)
    persist(serial)
    write_state(effect=profile)
    return body


def is_black(node):
    """True for a colour that renders as nothing: missing, or all channels 0."""
    if not node:
        return True
    return not any(node.get(k, 0) for k in ("red", "green", "blue"))


def payload_from(serial, profile, current):
    start, end = current.get("start"), current.get("end")
    # Measured 2026-10-08: `gradient`, `colorwarp` and `watercolor` carry a black
    # start and end, so applying them literally leaves the keyboard dark — it
    # looks exactly like `off` while being a different profile. A colourless
    # effect gets the theme's palette instead. `off` keeps its black: dark is the
    # point there, and it is the one profile where black is meant.
    if profile != "off" and is_black(start) and is_black(end):
        palette = theme_palette()
        if palette:
            start = color_node(hex_to_rgb(palette[0]))
            end = color_node(hex_to_rgb(palette[1] if len(palette) > 1 else palette[0]))
    return {
        "deviceId": serial,
        "profile": profile,
        "startColor": start,
        "endColor": end,
        "middleColor": current.get("middle"),
        "speed": clamp_speed(current.get("speed", 1)),
        "alternateColors": current.get("alternateColors", False),
        "rgbDirection": current.get("rgbDirection", 0),
    }


def set_brightness(serial, level):
    body = http("/api/brightness", {"deviceId": serial, "brightness": level}, method="POST")
    check(body, "brightness")
    persist(serial)
    write_state(brightness=level)
    return body


def status():
    state = read_state()
    out = {
        "ok": True,
        "connected": False,
        "serial": "",
        "product": PRODUCT,
        "firmware": "",
        "effect": state.get("effect", ""),
        "effect_name": "",
        "color": state.get("color", ""),
        # -1 until this panel has set a level: OpenLinkHub's stored 0 means "no
        # software level applied", not "dark", so it must not be shown as ours.
        "brightness": state.get("brightness", -1),
        "reported_brightness": -1,
        "theme_color": theme_color() or "",
        "theme_palette": theme_palette(),
        "follow_theme": bool(state.get("follow_theme", False)),
        "profiles": [],
    }
    try:
        serial, product = find_serial()
    except (urllib.error.URLError, ValueError, KeyError) as exc:
        out["error"] = "OpenLinkHub unreachable: %s" % exc
        return out
    out["connected"] = serial is not None
    out["product"] = product
    out["serial"] = serial or ""
    if not serial:
        return out

    try:
        colors = json.loads(http("/api/color/%s" % serial))["data"]
    except (urllib.error.URLError, ValueError, KeyError):
        return out

    out["profiles"] = [
        {"id": pid, "name": prof.get("profileName") or pid}
        for pid, prof in sorted(colors.get("profiles", {}).items())
        if pid not in NOT_A_KEYBOARD_EFFECT
    ]

    # OpenLinkHub's record of the device (50 kB of per-key layout); only two
    # scalars are wanted, and a regex survives the raw bytes in that payload.
    try:
        raw = http("/api/devices/%s" % serial)
        out["firmware"] = (re.search(r'"firmware":"([^"]*)"', raw) or [None, ""])[1]
        if '"Brightness"' in raw:
            out["reported_brightness"] = int(re.search(r'"Brightness":(\d+)', raw).group(1))
        record = (re.search(r'"RGBProfile":"([^"]*)"', raw) or [None, ""])[1]
        if record and not state.get("effect"):
            out["effect"] = record
        for entry in out["profiles"]:
            if entry["id"] == out["effect"]:
                out["effect_name"] = entry["name"]
    except (urllib.error.URLError, ValueError, AttributeError):
        pass

    return out


def selftest():
    """Offline checks for the parts that are easy to get quietly wrong."""
    assert hex_to_rgb("#30d158") == [48, 209, 88]
    assert hex_to_rgb("f80") == [255, 136, 0]
    assert rgb_to_hex({"red": 255, "green": 136, "blue": 0}) == "#ff8800"
    assert clamp_speed(0) == 1 and clamp_speed(99) == 10 and clamp_speed(None) == 1

    # The theme file is user-editable and outside this program's control, so the
    # parser is checked against what Omarchy's own themes write and against the
    # shapes that must be refused rather than guessed at.
    with tempfile.TemporaryDirectory() as tmp:
        def theme_file(content, name="keyboard.rgb"):
            path = os.path.join(tmp, name)
            with open(path, "w") as fh:
                fh.write(content)
            return path

        assert theme_color(theme_file("#e4c124\n")) == "#e4c124"   # this machine
        assert theme_color(theme_file("ff00ff")) == "#ff00ff"      # tokyo-night
        assert theme_color(theme_file("  FF00FF  \n")) == "#ff00ff"
        assert theme_color(theme_file("#abc")) is None             # short form
        assert theme_color(theme_file("#e4c124\n0xdeadbeef")) is None
        assert theme_color(theme_file("rgb(1,2,3)")) is None
        assert theme_color(os.path.join(tmp, "saknas")) is None

    # The palette, and the rule that a colourless effect must not stay dark while
    # `off` must. Both are checked against a theme written into the test's own
    # directory, so the machine's real theme cannot change the outcome.
    with tempfile.TemporaryDirectory() as tmp:
        colors = os.path.join(tmp, "colors.toml")

        def write_colors(text):
            with open(colors, "w") as fh:
                fh.write(text)
            return colors

        assert theme_palette(write_colors('accent = "#e4c124"\nselection = "#3B2A4D"\nforeground = "nonsense"\n')) \
            == ["#e4c124", "#3b2a4d"], "bad values are dropped, colours lower-cased"
        assert theme_palette(write_colors('accent = "#e4c124"\nselection = "#e4c124"\n')) == ["#e4c124"]
        assert theme_palette(write_colors("inte = 'toml'")) == []
        assert theme_palette(os.path.join(tmp, "saknas")) == []

        write_colors('accent = "#e4c124"\nselection = "#3b2a4d"\n')
        saved_colors = globals()["THEME_COLORS"]
        globals()["THEME_COLORS"] = colors
        try:
            black = {"start": {"red": 0, "green": 0, "blue": 0}, "end": {"red": 0, "green": 0, "blue": 0}}
            got = payload_from("S", "gradient", dict(black))["startColor"]
            assert (got["red"], got["green"], got["blue"]) == (0xE4, 0xC1, 0x24), got
            end = payload_from("S", "gradient", dict(black))["endColor"]
            assert (end["red"], end["green"], end["blue"]) == (0x3B, 0x2A, 0x4D), end
            assert payload_from("S", "off", dict(black))["startColor"]["red"] == 0, "off must stay dark"
            half = {"start": {"red": 0, "green": 0, "blue": 0}}
            assert payload_from("S", "watercolor", half)["endColor"]["red"] == 0x3B, "missing end -> palette[1]"
            live = {"start": {"red": 255, "green": 0, "blue": 0}, "end": {"red": 0, "green": 255, "blue": 0}}
            assert payload_from("S", "circle", dict(live))["endColor"]["green"] == 255, "stored colour kept"
        finally:
            globals()["THEME_COLORS"] = saved_colors

    check('{"code":200,"status":1,"message":"ok"}', "x")
    try:
        check('{"code":200,"status":0,"message":"Invalid speed"}', "colour")
        raise AssertionError("a refusal must not pass")
    except RuntimeError as exc:
        assert "Invalid speed" in str(exc)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            hex_to_rgb("red")
        raise AssertionError("a bad colour must not pass")
    except SystemExit:
        pass
    print(json.dumps({"ok": True, "selftest": "passed"}))


def main(argv):
    if len(argv) < 2:
        fail("no command")
    cmd, args = argv[1], argv[2:]

    if cmd == "selftest":
        selftest()
        return

    if cmd == "status":
        print(json.dumps(status()))
        return

    serial, _ = find_serial()
    # `theme off` is a state flag, not a keyboard action: it must work with the
    # keyboard unplugged (or OpenLinkHub down) so following can always be stopped.
    theme_off = cmd == "theme" and bool(args) and args[0].lower() in ("off", "av")
    if not serial and not theme_off:
        fail("keyboard not found (OpenLinkHub running?)")

    try:
        if cmd == "color":
            set_color(serial, args[0])
        elif cmd == "brightness":
            set_brightness(serial, max(0, min(3, int(args[0]))))
        elif cmd == "effect":
            set_effect(serial, args[0])
        elif cmd == "theme":
            cmd_theme(serial, args)
        elif cmd == "off":
            set_effect(serial, "off")
        else:
            fail("unknown command: %s" % cmd)
    except (urllib.error.URLError, ValueError, KeyError, RuntimeError) as exc:
        fail("%s: %s" % (cmd, exc))
    finally:
        record_action(cmd, args)

    print(json.dumps(status()))


if __name__ == "__main__":
    main(sys.argv)
