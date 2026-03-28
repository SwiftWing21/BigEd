"""Async RCON client for Factorio headless server (Source RCON v1 protocol)."""
import asyncio
import struct
import logging

log = logging.getLogger("biged.factorio.rcon")

SERVERDATA_AUTH = 3
SERVERDATA_AUTH_RESPONSE = 2
SERVERDATA_EXECCOMMAND = 2
SERVERDATA_RESPONSE_VALUE = 0


def encode_packet(request_id: int, packet_type: int, body: str) -> bytes:
    """Encode a Source RCON packet."""
    body_bytes = body.encode("utf-8")
    payload = struct.pack("<ii", request_id, packet_type) + body_bytes + b"\x00\x00"
    return struct.pack("<i", len(payload)) + payload


def decode_packet(data: bytes) -> tuple[int, int, str]:
    """Decode a Source RCON packet. Returns (request_id, packet_type, body)."""
    if len(data) < 14:
        raise ValueError(f"Packet too short: {len(data)} bytes")
    size = struct.unpack("<i", data[:4])[0]
    request_id = struct.unpack("<i", data[4:8])[0]
    packet_type = struct.unpack("<i", data[8:12])[0]
    body = data[12 : 12 + size - 10]
    return request_id, packet_type, body.decode("utf-8", errors="replace")


class RCONClient:
    """Async RCON client with reconnection and timeout support."""

    def __init__(self, host: str, port: int, password: str, timeout: float = 5.0):
        self.host = host
        self.port = port
        self.password = password
        self.timeout = timeout
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._request_id = 0
        self._connected = False

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    async def connect(self) -> None:
        """Connect and authenticate with the RCON server."""
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port),
            timeout=self.timeout,
        )
        auth_id = self._next_id()
        self._writer.write(encode_packet(auth_id, SERVERDATA_AUTH, self.password))
        await self._writer.drain()
        response = await self._read_packet()
        if response[0] == -1:
            raise ConnectionRefusedError("RCON authentication failed")
        self._connected = True
        log.info(f"RCON connected to {self.host}:{self.port}")

    async def disconnect(self) -> None:
        """Close the RCON connection."""
        if self._writer:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass
        self._connected = False
        self._reader = None
        self._writer = None

    async def command(self, cmd: str) -> str:
        """Send a command and return the response body."""
        if not self._connected:
            raise ConnectionError("Not connected to RCON server")
        req_id = self._next_id()
        self._writer.write(encode_packet(req_id, SERVERDATA_EXECCOMMAND, cmd))
        await self._writer.drain()
        resp_id, resp_type, body = await self._read_packet()
        return body

    async def _read_packet(self) -> tuple[int, int, str]:
        """Read one RCON packet from the stream."""
        size_data = await asyncio.wait_for(
            self._reader.readexactly(4), timeout=self.timeout
        )
        size = struct.unpack("<i", size_data)[0]
        payload = await asyncio.wait_for(
            self._reader.readexactly(size), timeout=self.timeout
        )
        return decode_packet(size_data + payload)

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *exc):
        await self.disconnect()
