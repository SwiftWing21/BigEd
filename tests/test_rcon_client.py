"""Tests for Factorio RCON client — packet framing + command encoding."""
import struct
import pytest


def test_encode_auth_packet():
    from factorio.rcon_client import encode_packet
    packet = encode_packet(1, 3, "mypassword")
    size = struct.unpack("<i", packet[:4])[0]
    req_id = struct.unpack("<i", packet[4:8])[0]
    ptype = struct.unpack("<i", packet[8:12])[0]
    body = packet[12:-2]
    assert req_id == 1
    assert ptype == 3  # AUTH
    assert body == b"mypassword"
    assert packet[-2:] == b"\x00\x00"
    assert size == len(packet) - 4


def test_encode_command_packet():
    from factorio.rcon_client import encode_packet
    packet = encode_packet(42, 2, "/biged-state")
    req_id = struct.unpack("<i", packet[4:8])[0]
    ptype = struct.unpack("<i", packet[8:12])[0]
    body = packet[12:-2]
    assert req_id == 42
    assert ptype == 2
    assert body == b"/biged-state"


def test_decode_response_packet():
    from factorio.rcon_client import encode_packet, decode_packet
    body = b'{"tick": 100}'
    payload = struct.pack("<ii", 42, 0) + body + b"\x00\x00"
    raw = struct.pack("<i", len(payload)) + payload
    req_id, ptype, data = decode_packet(raw)
    assert req_id == 42
    assert ptype == 0
    assert data == '{"tick": 100}'


def test_decode_empty_response():
    from factorio.rcon_client import encode_packet, decode_packet
    payload = struct.pack("<ii", 1, 0) + b"\x00\x00"
    raw = struct.pack("<i", len(payload)) + payload
    req_id, ptype, data = decode_packet(raw)
    assert data == ""
