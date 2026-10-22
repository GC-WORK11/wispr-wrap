#!/usr/bin/env python3
"""wfclip — multi-target X clipboard owner (stays resident until selection is lost).
Usage: wfclip.py <file> [mime]
  image mime -> offers: <mime> bytes + uri-list + gnome-copied-files + path text
  other      -> offers: uri-list + gnome-copied-files + path text
"""
import os, sys, urllib.parse
from Xlib import X, display, Xatom
from Xlib.protocol import event

path = sys.argv[1]
mime = sys.argv[2] if len(sys.argv) > 2 else ""
is_img = mime.startswith("image/")
uri = "file://" + urllib.parse.quote(path)
img_data = open(path, "rb").read() if is_img else None

d = display.Display()
root = d.screen().root
CLIP = d.intern_atom("CLIPBOARD")
TARGETS = d.intern_atom("TARGETS")
TIMESTAMP = d.intern_atom("TIMESTAMP")
MULTIPLE = d.intern_atom("MULTIPLE")
ATOM = d.intern_atom("ATOM")
URI_LIST = d.intern_atom("text/uri-list")
GNOME_FILES = d.intern_atom("x-special/gnome-copied-files")
UTF8 = d.intern_atom("UTF8_STRING")
PLAIN = d.intern_atom("text/plain")
STRING = d.intern_atom("STRING")
MIME_ATOM = d.intern_atom(mime) if is_img else 0

offered = [TARGETS, TIMESTAMP, MULTIPLE] + ([MIME_ATOM] if is_img else []) \
        + [GNOME_FILES, URI_LIST, UTF8, PLAIN, STRING]

win = root.create_window(0, 0, 1, 1, 0, 0, X.InputOnly)

# grab a real timestamp: property change -> PropertyNotify carries server time
DUMMY = d.intern_atom("_WFCLIP_T")
win.change_attributes(event_mask=X.PropertyChangeMask)
win.change_property(DUMMY, Xatom.STRING, 8, b"x")
d.sync()
own_time = X.CurrentTime
while True:
    ev = d.next_event()
    if ev.type == X.PropertyNotify and ev.atom == DUMMY:
        own_time = ev.time
        break
win.change_attributes(event_mask=0)

win.set_selection_owner(CLIP, own_time)
d.sync()
if d.get_selection_owner(CLIP) != win:
    sys.exit("failed to own clipboard")


def payload_for(tgt_atom):
    if tgt_atom == MIME_ATOM and img_data is not None:
        return MIME_ATOM, img_data
    if tgt_atom == URI_LIST:
        return URI_LIST, (uri + "\r\n").encode()
    if tgt_atom == GNOME_FILES:
        return GNOME_FILES, ("copy\n" + uri + "\n").encode()
    if tgt_atom in (UTF8, PLAIN, STRING):
        return tgt_atom, path.encode()
    return None


while True:
    ev = d.next_event()
    if ev.type == X.SelectionClear:
        sys.exit(0)
    if ev.type != X.SelectionRequest:
        continue
    prop = ev.property
    if prop == X.NONE:
        prop = ev.target  # obsolete clients
    ok = False
    try:
        if ev.target == TARGETS:
            ev.requestor.change_property(prop, ATOM, 32, offered)
            ok = True
        elif ev.target == TIMESTAMP:
            ev.requestor.change_property(prop, Xatom.INTEGER, 32, [own_time])
            ok = True
        elif ev.target == MULTIPLE:
            ok = False  # clients request one-by-one; refuse MULTIPLE
        else:
            r = payload_for(ev.target)
            if r:
                type_atom, data = r
                ev.requestor.change_property(prop, type_atom, 8, data)
                ok = True
    except Exception:
        ok = False
    notify = event.SelectionNotify(
        time=ev.time, requestor=ev.requestor, selection=ev.selection,
        target=ev.target, property=prop if ok else X.NONE)
    ev.requestor.send_event(notify, propagate=False)
    d.flush()
