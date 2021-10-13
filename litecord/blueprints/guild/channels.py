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

from typing import List, Dict, Tuple, Optional
from quart import Blueprint, request, current_app as app, jsonify


from litecord.blueprints.auth import token_check

from litecord.errors import BadRequest
from litecord.enums import ChannelType

from litecord.schemas import validate, ROLE_UPDATE_POSITION, CHANNEL_UPDATE_POSITION, CHAN_CREATE
from litecord.blueprints.checks import guild_check, guild_owner_check, guild_perm_check
from litecord.common.guilds import create_guild_channel

bp = Blueprint("guild_channels", __name__)

PairList = List[Tuple[int, int]]

def gen_pairs(
    list_of_changes: List[Dict[str, int]],
    current_state: Dict[int, int],
    blacklist: Optional[List[int]] = None,
) -> PairList:
    """Generate a list of pairs that, when applied to the database,
    will generate the desired state given in list_of_changes.

    We must check if the given list_of_changes isn't overwriting an
    element's (such as a role or a channel) position to an existing one,
    without there having an already existing change for the other one.

    Here's a pratical explanation with roles:

    R1 (in position RP1) wants to be in the same position
    as R2 (currently in position RP2).

    So, if we did the simpler approach, list_of_changes
    would just contain the preferred change: (R1, RP2).

    With gen_pairs, there MUST be a (R2, RP1) in list_of_changes,
    if there is, the given result in gen_pairs will be a pair
    ((R1, RP2), (R2, RP1)) which is then used to actually
    update the roles' positions in a transaction.

    Parameters
    ----------
    list_of_changes:
        A list of dictionaries with ``id`` and ``position``
        fields, describing the preferred changes.
    current_state:
        Dictionary containing the current state of the list
        of elements (roles or channels). Points position
        to element ID.
    blacklist:
        List of IDs that shouldn't be moved.

    Returns
    -------
    list
        List of swaps to do to achieve the preferred
        state given by ``list_of_changes``.
    """
    pairs: PairList = []
    blacklist = blacklist or []

    preferred_state = []
    for chan in current_state:
        preferred_state.insert(chan, current_state[chan])

    for blacklisted_id in blacklist:
        if blacklisted_id in preferred_state:
            preferred_state.remove(blacklisted_id)

    current_state = preferred_state.copy()

    # for each change, we must find a matching change
    # in the same list, so we can make a swap pair
    for change in list_of_changes:
        _id, pos = change['id'], change['position']
        if _id not in preferred_state:
            continue

        preferred_state.remove(_id)
        preferred_state.insert(pos, _id)

    assert len(current_state) == len(preferred_state)

    for i in range(len(current_state)):
        if current_state[i] != preferred_state[i]:
            pairs.append((preferred_state[i], i))

    return pairs

@bp.route("/<int:guild_id>/channels", methods=["GET"])
async def get_guild_channels(guild_id):
    """Get the list of channels in a guild."""
    user_id = await token_check()
    await guild_check(user_id, guild_id)

    return jsonify(await app.storage.get_channel_data(guild_id))


@bp.route("/<int:guild_id>/channels", methods=["POST"])
async def create_channel(guild_id):
    """Create a channel in a guild."""
    user_id = await token_check()
    j = validate(await request.get_json(), CHAN_CREATE)

    await guild_check(user_id, guild_id)
    await guild_perm_check(user_id, guild_id, "manage_channels")

    channel_type = j.get("type", ChannelType.GUILD_TEXT)
    channel_type = ChannelType(channel_type)

    if channel_type not in (ChannelType.GUILD_TEXT, ChannelType.GUILD_CATEGORY, ChannelType.GUILD_VOICE):
        raise BadRequest("Invalid channel type")

    new_channel_id = app.winter_factory.snowflake()
    await create_guild_channel(guild_id, new_channel_id, channel_type, **j)

    chan = await app.storage.get_channel(new_channel_id)
    await app.dispatcher.guild.dispatch(guild_id, ("CHANNEL_CREATE", chan))

    return jsonify(chan), 201


async def _chan_update_dispatch(guild_id: int, channel_id: int):
    """Fetch new information about the channel and dispatch
    a single CHANNEL_UPDATE event to the guild."""
    chan = await app.storage.get_channel(channel_id)
    await app.dispatcher.guild.dispatch(guild_id, ("CHANNEL_UPDATE", chan))


async def _do_single_swap(guild_id: int, updates: list):
    """Do a single channel swap, dispatching
    the CHANNEL_UPDATE events for after the swap"""
    updated = []
    # do the swap in a transaction.
    conn = await app.db.acquire()
    for pair in updates:
        _id, pos = pair

        async with conn.transaction():
            await conn.execute(
                """
            UPDATE guild_channels
            SET position = $1
            WHERE id = $2 AND guild_id = $3
            """, pos, _id, guild_id)
        updated.append(_id)

    await app.db.release(conn)

    for _id in updated:
        await _chan_update_dispatch(guild_id, _id)


def _group_channel(chan):
    """Swap channel pairs' positions, given the list
    of pairs to do.

    Dispatches CHANNEL_UPDATEs to the guild.
    """
    if ChannelType(chan['type']) == ChannelType.GUILD_CATEGORY:
        return 'c'
    elif chan['parent_id'] is None:
        return 'n'
    return chan['parent_id']


@bp.route("/<int:guild_id>/channels", methods=["PATCH"])
async def modify_channel_pos(guild_id):
    """Change positions of channels in a guild."""
    user_id = await token_check()

    await guild_owner_check(user_id, guild_id)
    await guild_perm_check(user_id, guild_id, "manage_channels")

    # same thing as guild.roles, so we use
    # the same schema and all.
    raw_j = await request.get_json()
    j = validate({'channels': raw_j}, CHANNEL_UPDATE_POSITION)
    j = j['channels']

    channels = {int(chan['id']): chan for chan in await app.storage.get_channel_data(guild_id)}
    channel_tree = {}

    for chan in j:
        conn = await app.db.acquire()
        _id = int(chan['id'])
        if _id in channels and 'parent_id' in chan and (chan['parent_id'] is None or chan['parent_id'] in channels):
            channels[_id]['parent_id'] = chan['parent_id']
            await conn.execute("""
            UPDATE guild_channels
            SET parent_id = $1
            WHERE id = $2 AND guild_id = $3
            """, chan['parent_id'], chan['id'], guild_id)

            await _chan_update_dispatch(guild_id, chan['id'])
        await app.db.release(conn)

    for chan in channels.values():
        channel_tree.setdefault(_group_channel(chan), []).append(chan)

    for _key in channel_tree:
        _channels = channel_tree[_key]
        _channel_ids = [int(chan['id']) for chan in _channels]
        _channel_positions = {chan['position']: int(chan['id'])
                              for chan in _channels}
        _change_list = list(filter(lambda chan: 'position' in chan and int(chan['id']) in _channel_ids, j))
        _swap_pairs = gen_pairs(
            _change_list,
            _channel_positions
        )

    await _do_single_swap(guild_id, _swap_pairs)
    return "", 204
