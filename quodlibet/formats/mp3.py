# Copyright 2004-2005 Joe Wreschnig, Michael Urman, Niklas Janlert 
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation
#
# $Id$

from formats.audio import AudioFile, AudioPlayer
import config
import re
import tempfile
try: import mutagen.id3, mad
except ImportError: extensions = []
else: extensions = [".mp3", ".mp2"]

def isascii(s): return ((len(s) == 0) or (ord(max(s)) < 128))

class ID3hack(mutagen.id3.ID3):
    "Override 'correct' behavior with desired behavior"
    def loaded_frame(self, name, tag):
        if tag.HashKey in self and tag.FrameID[0] == "T":
            self[tag.HashKey].extend(tag[:])
        else: self[tag.HashKey] = tag

# ID3 is absolutely the worst thing ever.

class MP3File(AudioFile):

    # http://www.unixgods.org/~tilo/ID3/docs/ID3_comparison.html
    # http://www.id3.org/id3v2.4.0-frames.txt
    IDS = { "TIT1": "grouping",
            "TIT2": "title",
            "TIT3": "version",
            "TPE1": "artist",
            "TPE2": "performer", 
            "TPE3": "conductor",
            "TPE4": "arranger",
            "TEXT": "lyricist",
            "TCOM": "composer",
            "TENC": "encodedby",
            "TLAN": "language",
            "TALB": "album",
            "TRCK": "tracknumber",
            "TPOS": "discnumber",
            "TSRC": "isrc",
            "TCOP": "copyright",
            "TPUB": "organization",
            "TSST": "part",
            "TOLY": "author",
            "TMOO": "mood",
            "TBPM": "bpm",
            "TDRC": "date",
            "WOAR": "website",
            }

    SDI = dict([(v, k) for k, v in IDS.iteritems()])

    CODECS = ["utf-8"]
    try: CODECS.extend(config.get("editing", "id3encoding").strip().split())
    except: pass # Uninitialized config...
    CODECS.append("iso-8859-1")

    def __init__(self, filename):
        try: tag = ID3hack(filename)
        except mutagen.id3.error: tag = {}

        for frame in tag.values():
            if frame.FrameID == "APIC" and len(frame.data):
                self["~picture"] = "y"
                continue
            elif frame.FrameID == "TCON":
                self["genre"] = "\n".join(frame.genres)
                continue
            elif frame.FrameID in ["COMM", "TXXX"]:
                if frame.desc.startswith("QuodLibet::"):
                    name = frame.desc[11:]
                elif frame.desc == "ID3v1 Comment": continue
                else: name = "comment"
            else: name = self.IDS.get(frame.FrameID, "").lower()

            if not name: continue

            id3id = frame.FrameID
            if id3id.startswith("T"):
                text = "\n".join(map(unicode, frame.text))
            elif id3id == "COMM" and frame.desc == "":
                text = "\n".join(frame.text)
            elif id3id.startswith("W"):
                text = frame.url
                frame.encoding = 0
            else: continue

            if not text: continue
            text = self.__distrust_latin1(text, frame.encoding)
            if text is None: continue

            if name in self: self[name] += "\n" + text
            else: self[name] = text
            self[name] = self[name].strip()

        import mad
        audio = mad.MadFile(filename)
        audio.seek_time(audio.total_time())
        audio.read()
        self["~#bitrate"] = audio.bitrate()
        self["~#length"] = audio.total_time() / 1000

        self.sanitize(filename)

    def __distrust_latin1(self, text, encoding):
        assert isinstance(text, unicode)
        if encoding == 0:
            text = text.encode('iso-8859-1')
            for codec in self.CODECS:
                try: text = text.decode(codec)
                except (UnicodeError, LookupError): pass
                else: break
            else: return None
        return text

    def write(self):
        tag = mutagen.id3.ID3(self['~filename'])
        tag.delall("COMM:QuodLibet:")
        tag.delall("TXXX:QuodLibet:")

        for key, id3name in self.SDI.items():
            tag.delall(id3name)

            if key not in self: continue
            elif not isascii(self[key]): enc = 1
            else: enc = 3

            Kind = mutagen.id3.Frames[id3name]
            text = self[key].split("\n")
            if id3name == "WOAR":
                for t in text:
                    tag.loaded_frame(id3name, Kind(url=t))
            else: tag.loaded_frame(id3name, Kind(encoding=enc, text=text))

        for key in filter(lambda x: x not in self.SDI and x not in ['genre'],
                          self.realkeys()):
            if not isascii(self[key]): enc = 1
            else: enc = 3
            f = mutagen.id3.TXXX(
                encoding=enc, text=self[key].split("\n"),
                desc=u"QuodLibet::%s" % key)
            tag.loaded_frame("TXXX", f)

        if "genre" in self:
            if not isascii(self["genre"]): enc = 1
            else: enc = 3
            t = self["genre"].split("\n")
            tag.loaded_frame("TCON", mutagen.id3.TCON(encoding=enc, text=t))
        else: del(tag["TCON"])

        tag.save()
        self.sanitize()

    def get_format_cover(self):
        f = tempfile.NamedTemporaryFile()
        tag = mutagen.id3.ID3(self["~filename"])
        for frame in tag.getall("APIC"):
            f.write(frame.data)
            f.flush()
            f.seek(0, 0)
            return f
        else:
            f.close()
            return None

class MP3Player(AudioPlayer):
    def __init__(self, dev, song):
        import mad
        filename = song['~filename']
        AudioPlayer.__init__(self)
        self.dev = dev
        audio = mad.MadFile(filename)
        # Lots of MP3s report incorrect bitrates/samplerates/lengths if
        # the ID3 tag is busted in whatever way the ID3 tag reader is
        # using. Seek to the end of the file to get relaible information.
        audio.seek_time(audio.total_time())
        audio.read()
        self.filename = song["~filename"]
        self.__expected_sr = audio.samplerate()
        self.dev.set_info(self.__expected_sr, 2)
        self.length = audio.total_time()
        # Then, reload so we don't get repeat audio.
        self.audio = mad.MadFile(filename)
        self.replay_gain(song)

    def __iter__(self): return self

    def seek(self, ms):
        self.audio.seek_time(int(ms))

    def next(self):
        if self.stopped: raise StopIteration
        buff = self.audio.read(256)
        if self.audio.samplerate() != self.__expected_sr:
            print "W: %s: Skipping what doesn't look like audio data..." % self.filename
            while self.audio.samplerate() != self.__expected_sr and buff:
                buff = self.audio.read(256)
            buff = self.audio.read(256)
        if buff is None: raise StopIteration
        self.dev.play(buff)
        return self.audio.current_time()

info = MP3File
player = MP3Player
