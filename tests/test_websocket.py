"""

Litecord
Copyright (C) 2018-2021  Luna Mendes and Litecord Contributors

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, version 3 of the License.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""

import json

import pytest
import websockets

from litecord.gateway.opcodes import OP
from litecord.gateway.websocket import decode_etf


async def _json(conn):
    frame = await conn.recv()
    return json.loads(frame)


async def _etf(conn):
    frame = await conn.recv()
    return decode_etf(frame)


async def _json_send(conn, data):
    frame = json.dumps(data)
    await conn.send(frame)


async def _json_send_op(conn, opcode, data=None):
    await _json_send(conn, {"op": opcode, "d": data})


async def _close(conn):
    await conn.close(1000, "test end")


async def get_gw(test_cli, version: int) -> str:
    """Get the Gateway URL."""
    gw_resp = await test_cli.get(f"/api/v{version}/gateway")
    gw_json = await gw_resp.json
    return gw_json["url"]


async def gw_start(test_cli, *, version: int = 6, etf=False):
    """Start a websocket connection"""
    gw_url = await get_gw(test_cli, version)

    if etf:
        gw_url = f"{gw_url}?v={version}&encoding=etf"
    else:
        gw_url = f"{gw_url}?v={version}&encoding=json"

    return await websockets.connect(gw_url)


@pytest.mark.asyncio
async def test_gw(test_cli):
    """Test if the gateway connects and sends a proper
    HELLO payload."""
    conn = await gw_start(test_cli)

    hello = await _json(conn)
    assert hello["op"] == OP.HELLO

    assert isinstance(hello["d"], dict)
    assert isinstance(hello["d"]["heartbeat_interval"], int)
    assert isinstance(hello["d"]["_trace"], list)

    await _close(conn)


@pytest.mark.asyncio
async def test_ready(test_cli_user):
    conn = await gw_start(test_cli_user.cli)

    # get the hello frame but ignore it
    await _json(conn)

    await _json_send(
        conn, {"op": OP.IDENTIFY, "d": {"token": test_cli_user.user["token"]}}
    )

    # try to get a ready
    try:
        await _json(conn)
        assert True
    except (Exception, websockets.ConnectionClosed):
        assert False
    finally:
        await _close(conn)


@pytest.mark.asyncio
async def test_broken_identify(test_cli_user):
    conn = await gw_start(test_cli_user.cli)

    # get the hello frame but ignore it
    await _json(conn)

    await _json_send(conn, {"op": OP.IDENTIFY, "d": {"token": True}})

    # try to get a ready
    try:
        await _json(conn)
        raise AssertionError("Received a JSON message but expected close")
    except websockets.ConnectionClosed as exc:
        assert exc.code == 4002
    finally:
        await _close(conn)


@pytest.mark.asyncio
async def test_ready_fields(test_cli_user):
    conn = await gw_start(test_cli_user.cli)

    # get the hello frame but ignore it
    await _json(conn)

    await _json_send(
        conn, {"op": OP.IDENTIFY, "d": {"token": test_cli_user.user["token"]}}
    )

    try:
        ready = await _json(conn)
        assert isinstance(ready, dict)
        assert ready["op"] == OP.DISPATCH
        assert ready["t"] == "READY"

        data = ready["d"]
        assert isinstance(data, dict)

        # NOTE: change if default gateway changes
        assert data["v"] == 6

        # make sure other fields exist and are with
        # proper types.
        assert isinstance(data["user"], dict)
        assert isinstance(data["private_channels"], list)
        assert isinstance(data["guilds"], list)
        assert isinstance(data["session_id"], str)
        assert isinstance(data["_trace"], list)

        if "shard" in data:
            assert isinstance(data["shard"], list)
    finally:
        await _close(conn)


@pytest.mark.asyncio
async def test_ready_v9(test_cli_user):
    conn = await gw_start(test_cli_user.cli, version=9)
    await _json(conn)
    await _json_send(
        conn, {"op": OP.IDENTIFY, "d": {"token": test_cli_user.user["token"]}}
    )

    try:
        ready = await _json(conn)
        assert isinstance(ready, dict)
        assert ready["op"] == OP.DISPATCH
        assert ready["t"] == "READY"

        data = ready["d"]
        assert isinstance(data, dict)
        assert data["v"] == 9
        assert isinstance(data["user"], dict)
        assert isinstance(data["relationships"], list)

        ready = await _json(conn)
        assert isinstance(ready, dict)
        assert ready["op"] == OP.DISPATCH
        assert ready["t"] == "READY_SUPPLEMENTAL"
    finally:
        await _close(conn)


@pytest.mark.asyncio
async def test_heartbeat(test_cli_user):
    conn = await gw_start(test_cli_user.cli)

    # get the hello frame but ignore it
    await _json(conn)

    await _json_send(
        conn, {"op": OP.IDENTIFY, "d": {"token": test_cli_user.user["token"]}}
    )

    # ignore ready data
    ready = await _json(conn)
    assert isinstance(ready, dict)
    assert ready["op"] == OP.DISPATCH
    assert ready["t"] == "READY"

    # test a heartbeat
    await _json_send_op(conn, OP.HEARTBEAT)
    recv = await _json(conn)
    assert isinstance(recv, dict)
    assert recv["op"] == OP.HEARTBEAT_ACK

    await _close(conn)


@pytest.mark.asyncio
async def test_etf(test_cli):
    """Test if the websocket can send a HELLO message over ETF."""
    conn = await gw_start(test_cli, etf=True)

    try:
        hello = await _etf(conn)
        assert hello["op"] == OP.HELLO
    finally:
        await _close(conn)


@pytest.mark.asyncio
async def test_resume(test_cli_user):
    conn = await gw_start(test_cli_user.cli)

    # get the hello frame but ignore it
    await _json(conn)

    await _json_send(
        conn, {"op": OP.IDENTIFY, "d": {"token": test_cli_user.user["token"]}}
    )

    try:
        ready = await _json(conn)
        assert isinstance(ready, dict)
        assert ready["op"] == OP.DISPATCH
        assert ready["t"] == "READY"

        data = ready["d"]
        assert isinstance(data, dict)

        assert isinstance(data["session_id"], str)
        sess_id: str = data["session_id"]
    finally:
        await _close(conn)

    # try to resume
    conn = await gw_start(test_cli_user.cli)
    _ = await _json(conn)

    await _json_send(
        conn,
        {
            "op": OP.RESUME,
            "d": {
                "token": test_cli_user.user["token"],
                "session_id": sess_id,
                "seq": 0,
            },
        },
    )

    msg = await _json(conn)
    assert isinstance(msg, dict)
    assert isinstance(msg["op"], int)
    assert msg["op"] == OP.DISPATCH
    assert isinstance(msg["t"], str)
    assert msg["t"] in ("RESUMED", "PRESENCE_REPLACE")

    # close again, and retry again, but this time by removing the state
    # and asserting the session won't be resumed.
    await _close(conn)

    conn = await gw_start(test_cli_user.cli)
    _ = await _json(conn)

    async with test_cli_user.app.app_context():
        test_cli_user.app.state_manager.remove(sess_id)

    await _json_send(
        conn,
        {
            "op": OP.RESUME,
            "d": {
                "token": test_cli_user.user["token"],
                "session_id": sess_id,
                "seq": 0,
            },
        },
    )

    msg = await _json(conn)
    assert isinstance(msg, dict)
    assert isinstance(msg["op"], int)
    assert msg["op"] == OP.INVALID_SESSION
