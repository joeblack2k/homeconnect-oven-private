"""Constants for Home Connect Oven Private."""

from homeassistant.const import Platform

DOMAIN = "homeconnect_oven_private"
NAME = "Home Connect Oven Private"
VERSION = "0.1.0"

PLATFORMS = [
    Platform.CAMERA,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SWITCH,
    Platform.NUMBER,
]

OAUTH_BASE = "https://api.home-connect.com"
PRIVATE_API_HOST = "https://na.services.home-connect.com"
ENDPOINT_AUTHORIZE = "/security/oauth/authorize"
ENDPOINT_TOKEN = "/security/oauth/token"
PRIVATE_CLIENT_ID = "9B75AC9EC512F36C84256AC47D813E2C1DD0D6520DF774B020E1E6E2EB29B1F3"
PRIVATE_REDIRECT_URI = "https://qr.home-connect.com/authorize/prod/"
PRIVATE_SCOPES = (
    "Control DeleteAppliance IdentifyAppliance Images Monitor ReadAccount "
    "ReadOrigApi Settings WriteAppliance WriteOrigApi"
)

PRIVATE_TOKEN_STORE_VERSION = 1
PRIVATE_TOKEN_STORE_KEY = f"{DOMAIN}_token"
PRIVATE_PROBE_STORE_VERSION = 1
PRIVATE_PROBE_STORE_KEY = f"{DOMAIN}_probe"

PRIVATE_ACCOUNT_CAMERA_ENDPOINT = "/account/camera"
PRIVATE_ACCOUNT_CAMERA_ACCEPTS = (
    "application/vnd.bsh.hca.v2+json",
    "application/vnd.bsh.hca.v1+json",
)

DEFAULT_POLL_INTERVAL = 5
MIN_POLL_INTERVAL = 5
DEFAULT_VIDEO_PROBE_INTERVAL = 300
DEFAULT_VIDEO_DOWNLOAD_DIR = "www/homeconnect_oven_private"
PKCE_EXPIRY_SECONDS = 900

CONF_CALLBACK_URL = "callback_url"
CONF_POLL_INTERVAL = "poll_interval"
CONF_DIAGNOSTIC_SENSORS = "diagnostic_sensors"
CONF_VIDEO_DOWNLOAD_DIR = "video_download_dir"

OPTION_DEFAULTS = {
    CONF_POLL_INTERVAL: DEFAULT_POLL_INTERVAL,
    CONF_DIAGNOSTIC_SENSORS: False,
    CONF_VIDEO_DOWNLOAD_DIR: DEFAULT_VIDEO_DOWNLOAD_DIR,
}

PROBE_ROUTE_DEFINITIONS = {
    "snapshot_latest_image": "/ui/app/v1/appliances/{ha_id}/oven/media/latest?type=image",
    "snapshot_latest_video": "/ui/app/v1/appliances/{ha_id}/oven/media/latest?type=video",
    "snapshot_gallery": "/ui/app/v1/appliances/{ha_id}/oven/media/gallery",
    "media_latest": "/api/media/v1/{ha_id}/media/latest",
    "media_latest_video": "/api/media/v1/{ha_id}/media/latest?type=video",
}

