"""Silent fake MPRIS player for testing the seekbar plugin."""
import sys
import time
from gi.repository import Gio, GLib

ARTDIR = sys.argv[1]
TRACKS = [("Alpha Song", "Test Artist", 200, "red"), ("Beta Song", "Test Artist", 150, "green"),
          ("Gamma Song", "Other Artist", 300, "blue")]
XML = """<node>
<interface name="org.mpris.MediaPlayer2"><property name="Identity" type="s" access="read"/></interface>
<interface name="org.mpris.MediaPlayer2.Player">
<method name="PlayPause"/><method name="Play"/><method name="Pause"/><method name="Stop"/><method name="Next"/><method name="Previous"/>
<method name="Seek"><arg name="o" type="x" direction="in"/></method>
<method name="SetPosition"><arg name="t" type="o" direction="in"/><arg name="p" type="x" direction="in"/></method>
<property name="PlaybackStatus" type="s" access="read"/><property name="Metadata" type="a{sv}" access="read"/>
<property name="Position" type="x" access="read"/><property name="CanSeek" type="b" access="read"/>
<property name="CanControl" type="b" access="read"/><property name="CanPlay" type="b" access="read"/>
<property name="CanPause" type="b" access="read"/><property name="CanGoNext" type="b" access="read"/>
<property name="CanGoPrevious" type="b" access="read"/><property name="Rate" type="d" access="read"/>
</interface></node>"""

st = {"i": 0, "playing": True, "base": 0.0, "t0": time.monotonic()}


def pos():
    return st["base"] + ((time.monotonic() - st["t0"]) if st["playing"] else 0)


def setpos(p):
    st["base"], st["t0"] = p, time.monotonic()


def meta():
    t, a, l, c = TRACKS[st["i"]]
    return {"mpris:trackid": GLib.Variant("o", "/t/%d" % st["i"]), "xesam:title": GLib.Variant("s", t),
            "xesam:artist": GLib.Variant("as", [a]), "mpris:length": GLib.Variant("x", l * 1000000),
            "mpris:artUrl": GLib.Variant("s", "file://%s/art_%s.png" % (ARTDIR, c))}


conn = None


def changed(**props):
    conn.emit_signal(None, "/org/mpris/MediaPlayer2", "org.freedesktop.DBus.Properties", "PropertiesChanged",
                     GLib.Variant("(sa{sv}as)", ("org.mpris.MediaPlayer2.Player", props, [])))


def call(c, sender, path, iface, method, params, inv):
    if method in ("PlayPause", "Play", "Pause"):
        cur = pos()
        st["playing"] = {"PlayPause": not st["playing"], "Play": True, "Pause": False}[method]
        setpos(cur)
    elif method in ("Next", "Previous"):
        st["i"] = (st["i"] + (1 if method == "Next" else -1)) % len(TRACKS)
        setpos(0)
        changed(Metadata=GLib.Variant("a{sv}", meta()))
    elif method == "SetPosition":
        setpos(params.unpack()[1] / 1e6)
    elif method == "Seek":
        setpos(pos() + params.unpack()[0] / 1e6)
    print(time.strftime("%H:%M:%S"), "CALL", method, params.unpack() if params else "",
          "-> track", TRACKS[st["i"]][0], "pos", round(pos(), 1), "playing", st["playing"], flush=True)
    changed(PlaybackStatus=GLib.Variant("s", "Playing" if st["playing"] else "Paused"))
    inv.return_value(None)


def getp(c, sender, path, iface, prop):
    v = {"Identity": GLib.Variant("s", "cctest"),
         "PlaybackStatus": GLib.Variant("s", "Playing" if st["playing"] else "Paused"),
         "Metadata": GLib.Variant("a{sv}", meta()), "Position": GLib.Variant("x", int(pos() * 1e6)),
         "Rate": GLib.Variant("d", 1.0)}
    return v.get(prop, GLib.Variant("b", True))


node = Gio.DBusNodeInfo.new_for_xml(XML)


def acquired(c, name):
    global conn
    conn = c
    for i in node.interfaces:
        c.register_object("/org/mpris/MediaPlayer2", i, call, getp, None)


Gio.bus_own_name(Gio.BusType.SESSION, "org.mpris.MediaPlayer2.cctest", 0, acquired, None, None)
GLib.MainLoop().run()
