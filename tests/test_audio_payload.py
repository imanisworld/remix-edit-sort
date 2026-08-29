from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import audio_payload  # noqa: E402


def chunk(kind: bytes, payload: bytes, *, byteorder: str) -> bytes:
    body = kind + len(payload).to_bytes(4, byteorder) + payload
    return body + (b"\x00" if len(payload) & 1 else b"")


class AudioPayloadTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, name: str, payload: bytes) -> Path:
        path = self.tmp / name
        path.write_bytes(payload)
        return path

    @staticmethod
    def id3(tag: bytes) -> bytes:
        size = len(tag)
        encoded = bytes(
            ((size >> 21) & 0x7F, (size >> 14) & 0x7F, (size >> 7) & 0x7F, size & 0x7F)
        )
        return b"ID3\x04\x00\x00" + encoded + tag

    def test_mp3_ignores_id3v2_and_id3v1(self):
        audio = b"mpeg-frames"
        first = self.write("first.mp3", self.id3(b"small") + audio)
        second = self.write(
            "second.mp3", self.id3(b"larger-tag" * 100) + audio + b"TAG" + bytes(125)
        )
        self.assertEqual(audio_payload.payload_bytes(first), audio)
        self.assertEqual(
            audio_payload.payload_digest(first), audio_payload.payload_digest(second)
        )

    def test_m4a_hashes_only_mdat_boxes(self):
        def box(kind: bytes, body: bytes) -> bytes:
            return (len(body) + 8).to_bytes(4, "big") + kind + body

        audio = b"aac-payload"
        first = self.write("first.m4a", box(b"ftyp", b"one") + box(b"mdat", audio))
        second = self.write(
            "second.m4a", box(b"ftyp", b"different metadata") + box(b"mdat", audio)
        )
        self.assertEqual(
            audio_payload.payload_digest(first), audio_payload.payload_digest(second)
        )

    def test_flac_ignores_metadata_blocks(self):
        audio = b"flac-frames"
        first = self.write("first.flac", b"fLaC" + b"\x84\x00\x00\x01x" + audio)
        second = self.write(
            "second.flac", b"fLaC" + b"\x84\x00\x00\x05abcde" + audio
        )
        self.assertEqual(
            audio_payload.payload_digest(first), audio_payload.payload_digest(second)
        )

    def test_wave_ignores_id3_and_list_chunks(self):
        audio = b"pcm-samples"

        def wave(metadata: bytes) -> bytes:
            chunks = (
                chunk(b"fmt ", bytes(16), byteorder="little")
                + chunk(b"ID3 ", metadata, byteorder="little")
                + chunk(b"LIST", metadata[::-1], byteorder="little")
                + chunk(b"data", audio, byteorder="little")
            )
            return b"RIFF" + (len(chunks) + 4).to_bytes(4, "little") + b"WAVE" + chunks

        first = self.write("first.wav", wave(b"short"))
        second = self.write("second.wav", wave(b"much larger metadata" * 20))
        self.assertEqual(audio_payload.payload_bytes(first), audio)
        self.assertEqual(
            audio_payload.payload_digest(first), audio_payload.payload_digest(second)
        )

    def test_aiff_ignores_id3_chunks(self):
        audio = b"aiff-samples"

        def aiff(metadata: bytes) -> bytes:
            sound = bytes(8) + audio
            chunks = (
                chunk(b"COMM", bytes(18), byteorder="big")
                + chunk(b"ID3 ", metadata, byteorder="big")
                + chunk(b"SSND", sound, byteorder="big")
            )
            return b"FORM" + (len(chunks) + 4).to_bytes(4, "big") + b"AIFF" + chunks

        first = self.write("first.aiff", aiff(b"short"))
        second = self.write("second.aiff", aiff(b"much larger metadata" * 20))
        self.assertEqual(audio_payload.payload_bytes(first), audio)
        self.assertEqual(
            audio_payload.payload_digest(first), audio_payload.payload_digest(second)
        )

    def test_malformed_container_fails_closed(self):
        broken = self.write("broken.wav", b"not-wave")
        with self.assertRaisesRegex(RuntimeError, "RIFF/WAVE"):
            audio_payload.payload_digest(broken)


if __name__ == "__main__":
    unittest.main()
