"""Tests for rcon_client.py — packet encode/decode (no live server)."""
import struct
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "fleet"))

from factorio.rcon_client import (
    encode_packet,
    decode_packet,
    SERVERDATA_AUTH,
    SERVERDATA_AUTH_RESPONSE,
    SERVERDATA_EXECCOMMAND,
    SERVERDATA_RESPONSE_VALUE,
)


class TestConstants:
    def test_auth_type(self):
        assert SERVERDATA_AUTH == 3

    def test_auth_response_type(self):
        assert SERVERDATA_AUTH_RESPONSE == 2

    def test_execcommand_type(self):
        assert SERVERDATA_EXECCOMMAND == 2

    def test_response_value_type(self):
        assert SERVERDATA_RESPONSE_VALUE == 0


class TestPacketEncoding:
    def test_roundtrip(self):
        packet = encode_packet(42, SERVERDATA_AUTH, "password123")
        req_id, pkt_type, body = decode_packet(packet)
        assert req_id == 42
        assert pkt_type == SERVERDATA_AUTH
        assert body == "password123"

    def test_empty_body(self):
        packet = encode_packet(1, 0, "")
        req_id, pkt_type, body = decode_packet(packet)
        assert req_id == 1
        assert pkt_type == 0
        assert body == ""

    def test_packet_structure(self):
        """Verify wire format: 4-byte size + 4-byte id + 4-byte type + body + 2 null bytes."""
        packet = encode_packet(1, 2, "hello")
        size = struct.unpack("<i", packet[:4])[0]
        # size = id(4) + type(4) + body(5) + padding(2) = 15
        assert size == 4 + 4 + 5 + 2
        assert len(packet) == 4 + size  # size header + payload

    def test_short_packet_raises(self):
        with pytest.raises(ValueError, match="too short"):
            decode_packet(b"\x00" * 5)

    def test_large_request_id(self):
        packet = encode_packet(999999, SERVERDATA_EXECCOMMAND, "test")
        req_id, _, _ = decode_packet(packet)
        assert req_id == 999999

    def test_unicode_body(self):
        packet = encode_packet(1, 0, "hello \u00e9\u00e8\u00ea")
        _, _, body = decode_packet(packet)
        assert body == "hello \u00e9\u00e8\u00ea"

    def test_roundtrip_execcommand(self):
        cmd = '/c rcon.print(remote.call("biged", "get_state"))'
        packet = encode_packet(7, SERVERDATA_EXECCOMMAND, cmd)
        req_id, pkt_type, body = decode_packet(packet)
        assert req_id == 7
        assert pkt_type == SERVERDATA_EXECCOMMAND
        assert body == cmd

    def test_minimum_valid_packet(self):
        """Empty body still produces a valid packet (14 bytes minimum)."""
        packet = encode_packet(0, 0, "")
        assert len(packet) >= 14
        req_id, pkt_type, body = decode_packet(packet)
        assert req_id == 0
        assert body == ""
