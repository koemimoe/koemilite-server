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

import re
import asyncio
import urllib.parse
import urllib.request
import json
from pathlib import Path
from typing import List, Optional

from quart import current_app as app
from logbook import Logger

from litecord.embed.sanitizer import proxify, fetch_metadata, fetch_mediaproxy_embed
from litecord.embed.schemas import EmbedURL

log = Logger(__name__)


MEDIA_EXTENSIONS = ("png", "jpg", "jpeg", "gif", "webm")


async def fetch_mediaproxy_img_meta(url) -> Optional[dict]:
    """Insert media metadata as an embed."""
    img_proxy_url = proxify(url)
    meta = await fetch_metadata(url)

    if meta is None:
        return None

    if not meta["image"]:
        return None
        
    return {
        "type": "image",
        "url": url,
        "thumbnail": {
            "width": meta["width"],
            "height": meta["height"],
            "url": url,
            "proxy_url": img_proxy_url,
        },
    }

    
async def fetch_youtube_meta(url) -> Optional[dict]:
    """Insert media metadata as an embed."""
    
    if isinstance(url, EmbedURL):
        parsed = url.parsed
    else:
        parsed = urllib.parse.urlparse(url)
        
    getid = re.search(r"(?:https?:\/\/)?(?:www\.|m\.)?youtu(?:\.be\/|be.com\/\S*(?:watch|embed)(?:(?:(?=\/[^&\s\?]+(?!\S))\/)|(?:\S*v=|v\/)))([^&\s\?]+)", parsed.geturl())
    img_proxy_url = proxify("https://i.ytimg.com/vi/" + getid.group(1) + "/maxresdefault.jpg")
    meta = await fetch_metadata("https://i.ytimg.com/vi/" + getid.group(1) + "/maxresdefault.jpg")
    apireq = urllib.request.urlopen("https://www.googleapis.com/youtube/v3/videos?part=snippet%2Cstatistics&id="+getid.group(1)+"&key=AIzaSyDFeUbGje2CPPfQsGGSqsMQ99F1Nb4l0s4").read()
    apidata = json.loads(apireq)
    
    return {
        "type": "video",
        "url": url,
        "title": apidata["items"][0]["snippet"]["title"],
        "color": 16711680,
        "author": {
            "name": apidata["items"][0]["snippet"]["channelTitle"],
            "url": "https://www.youtube.com/channel/"+apidata["items"][0]["snippet"]["channelId"]
        },
        "provider": {
            "name": "YouTube",
            "url": "https://www.youtube.com"
        },
        "thumbnail": {
            "width": meta["width"],
            "height": meta["height"],
            "url": "https://i.ytimg.com/vi/" + getid.group(1) + "/maxresdefault.jpg",
            "proxy_url": img_proxy_url,
        },
        "video": {
            "url": "https://www.youtube.com/embed/" + getid.group(1),
            "width": 1280,
            "height": 720
        }
    }

async def fetch_soundcloud_meta(url) -> Optional[dict]:
    """Insert media metadata as an embed."""
    
    if isinstance(url, EmbedURL):
        parsed = url.parsed
    else:
        parsed = urllib.parse.urlparse(url)
    
    apireq = urllib.request.urlopen("https://api-widget.soundcloud.com/resolve?url="+parsed.geturl()+"&format=json&client_id=RCKzxQA0jl0HV4RjjQRrblyTQzvsfgsc&app_version=1633525845").read()
    apidata = json.loads(apireq)
    
    if apidata["artwork_url"]:
        artwork = apidata["artwork_url"].replace("large", "t500x500")
    else:
        artwork = apidata["user"]["avatar_url"].replace("large", "t500x500")
    
    img_proxy_url = proxify(artwork)
    meta = await fetch_metadata(artwork)
    
    return {
        "type": "video",
        "url": url,
        "title": apidata["title"],
        "color": 819,
        "author": {
            "name": apidata["user"]["username"],
            "url": apidata["user"]["permalink_url"]
        },
        "provider": {
            "name": "SoundCloud",
            "url": "https://www.soundcloud.com"
        },
        "thumbnail": {
            "width": meta["width"],
            "height": meta["height"],
            "url": artwork,
            "proxy_url": img_proxy_url,
        },
        "video": {
            "url": "https://w.soundcloud.com/player/?url=" + parsed.geturl() + "&auto_play=false&show_artwork=true&visual=true&origin=twitter",
            "width": 435,
            "height": 400
        }
    }
    
async def msg_update_embeds(payload, new_embeds):
    """Update the message with the given embeds and dispatch a MESSAGE_UPDATE
    to users."""

    message_id = int(payload["id"])
    channel_id = int(payload["channel_id"])

    await app.storage.execute_with_json(
        """
        UPDATE messages
        SET embeds = $1
        WHERE messages.id = $2
        """,
        new_embeds,
        message_id,
    )

    update_payload = {
        "id": str(message_id),
        "channel_id": str(channel_id),
        "embeds": new_embeds,
    }

    if "guild_id" in payload:
        update_payload["guild_id"] = payload["guild_id"]

    if "flags" in payload:
        update_payload["flags"] = payload["flags"]

    await app.dispatcher.channel.dispatch(
        channel_id, ("MESSAGE_UPDATE", update_payload)
    )


def is_media_url(url) -> bool:
    """Return if the given URL is a media url."""

    if isinstance(url, EmbedURL):
        parsed = url.parsed
    else:
        parsed = urllib.parse.urlparse(url)

    path = Path(parsed.path)
    extension = path.suffix.lstrip(".")

    return extension in MEDIA_EXTENSIONS
    
def is_youtube_url(url) -> bool:
    """Return if url is youtube"""
    
    if isinstance(url, EmbedURL):
        parsed = url.parsed
    else:
        parsed = urllib.parse.urlparse(url)
    
    match = re.search(r"(?:https?:\/\/)?(?:www\.|m\.)?youtu(?:\.be\/|be.com\/\S*(?:watch|embed)(?:(?:(?=\/[^&\s\?]+(?!\S))\/)|(?:\S*v=|v\/)))([^&\s\?]+)", parsed.geturl())
    if match:
        return True
    else:
        return False
        
def is_sc_url(url) -> bool:
    """Return if url is soundcloud"""
    
    if isinstance(url, EmbedURL):
        parsed = url.parsed
    else:
        parsed = urllib.parse.urlparse(url)
    
    match = re.search(r"^https?:\/\/(www\.soundcloud\.com|soundcloud\.com)\/(.*)$", parsed.geturl())
    if match:
        return True
    else:
        return False

async def process_url_embed(payload: dict, *, delay=0):
    """Process URLs in a message and generate embeds based on that."""
    await asyncio.sleep(delay)

    message_id = int(payload["id"])

    # if we already have embeds
    # we shouldn't add our own.
    embeds = payload["embeds"]

    if embeds:
        log.debug("url processor: ignoring existing embeds @ mid {}", message_id)
        return

    # now, we have two types of embeds:
    # - image embeds
    # - url embeds

    # use regex to get URLs
    urls = re.findall(r"(https?://\S+)", payload["content"])
    urls = urls[:5]

    # from there, we need to parse each found url and check its path.
    # if it ends with png/jpg/gif/some other extension, we treat it as
    # media metadata to fetch.

    # if it isn't, we forward an /embed/ scope call to mediaproxy
    # to generate an embed for us out of the url.

    new_embeds: List[dict] = []

    for upstream_url in urls:
        url = EmbedURL(upstream_url)
        
        if is_media_url(url):
            embed = await fetch_mediaproxy_img_meta(url)
            if embed is not None:
                embeds = [embed]
        elif is_youtube_url(url):
            embed = await fetch_youtube_meta(url)
            if embed is not None:
                embeds = [embed]
        elif is_sc_url(url):
            embed = await fetch_soundcloud_meta(url)
            if embed is not None:
                embeds = [embed]
        else:
            embeds = await fetch_mediaproxy_embed(url)

        if not embeds:
            continue

        new_embeds.extend(embeds)

    # update if we got embeds
    if not new_embeds:
        return

    log.debug("made {} embeds for mid {}", len(new_embeds), message_id)

    await msg_update_embeds(payload, new_embeds)
