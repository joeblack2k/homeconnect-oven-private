# Home Connect Oven Private

`homeconnect_oven_private` is a standalone Home Assistant custom integration for Siemens / Home Connect ovens that expose features in the official Android app but not in the documented public Home Connect developer API.

This integration is intentionally separate from `home_connect_alt`. It uses undocumented private mobile endpoints and can break if Home Connect changes those endpoints, scopes, or OAuth behavior.

## Current scope

Implemented:

- private oven still snapshot camera
- snapshot metadata sensors
- private oven notification-center sensors
- private timelapse/video probing
- button to download the latest timelapse video when the backend actually exposes one
- local timelapse recording from still snapshots when the backend exposes no video
- diagnostic sensors, disabled by default

Not shipped yet:

- oven light switch
- oven light brightness control
- camera enabled switch
- timelapse enabled switch

Those controls are intentionally withheld until their read/write contract is validated against live responses.

## Installation

1. Add this repository to HACS as a custom repository.
2. Install `Home Connect Oven Private`.
3. Restart Home Assistant.
4. Add the integration from **Settings -> Devices & services**.
5. Open the generated Home Connect private mobile authorization URL.
6. Complete login and paste the final `https://qr.home-connect.com/authorize/prod/?code=...` callback URL into the config flow.

## Options

- `poll_interval`: snapshot refresh interval in seconds, minimum `5`
- `diagnostic_sensors`: enables disabled-by-default technical sensors
- `video_download_dir`: relative `/config` directory where downloaded timelapse files are stored
- `local_timelapse_fps`: frame rate used when building a local MP4 timelapse, default `4`

The default download directory is `/config/www/homeconnect_oven_private`, which is served in Home Assistant as `/local/homeconnect_oven_private/...`.

## Timelapse notes

The Android app contains a dedicated video media flow and the APK shows `MediaType.Video` support. This integration probes the known candidate video endpoints at runtime. If no usable video metadata or binary media is returned for the oven, the timelapse entities stay empty and the download button will fail with a clear error.

Live validation against `HS958GED1` returned no downloadable timelapse for the probed routes:

- `/ui/app/v1/appliances/{ha_id}/oven/media/latest?type=video` returned an empty item list
- `/ui/app/v1/appliances/{ha_id}/oven/media/gallery` returned "This appliance type does not support galleries"
- `/api/media/v1/{ha_id}/media/latest` returned not found
- `/api/media/v1/{ha_id}/media/latest?type=video` rejected the `type` query parameter

The integration still exposes timelapse availability and download entities so future backend support shows up without changing the entity model.

When the backend does not expose a downloadable video, the integration can build
a local timelapse from the still snapshot camera:

1. Press **Start Local Timelapse Recording** while the oven is running.
2. The integration captures each new still snapshot media id as a JPEG frame.
3. Press **Stop Local Timelapse Recording** when the cook is done.
4. Press **Build Local Timelapse** to create `timelapse.mp4` in the configured
   download directory.

The resulting file is exposed by the `Local Timelapse File` sensor as a Home
Assistant `/local/...` URL when the configured directory is under `/config/www`.
MP4 generation uses `ffmpeg` if it is available in the Home Assistant runtime;
captured frames remain on disk even if MP4 generation is not available.

## Oven notifications

The official Android app push message for turning food is not derived from the
camera image. APK inspection and live traffic validation show that the app reads
oven appliance-event notifications from the private notification-center API:

- `GET /accounts/self/notifications?channel=center`
- required header: `Accept-Language`

For the validated `HS958GED1`, the endpoint returned localized oven events such
as:

- `Cooking.Oven.Event.Cavity.001.TurnFoodLater`
- `Cooking.Oven.Event.Cavity.001.TurnFoodNow`
- `Cooking.Oven.Event.Cavity.001.CloseDoor`
- `Cooking.Oven.Event.Cavity.001.ProgramFinished`

The component exposes the latest oven notification and the latest turn-food
notification as read-only sensors. The binary sensors only report `on` when the
backend itself marks the notification state as `present`; historical
notifications remain visible through timestamp/message sensors without inventing
an active state.

## Live validation

Validated locally in Home Assistant Core `2026.6.3` with a Siemens `HS958GED1`.

- `camera.hs958ged1_camera` served the latest still snapshot through Home Assistant as `image/jpeg`
- snapshot payload size was `677993` bytes at `2592x1952`
- snapshot metadata sensors reported `COMPLETED` upload status, closed door state, and camera temperature
- `binary_sensor.hs958ged1_latest_timelapse_available` was `off` because no video media was exposed by the probed private endpoints
- `sensor.hs958ged1_latest_turn_food_message` reported the localized "turn the dish" instruction from the private notification-center endpoint

## Supported model

This work was validated first against:

- `HS958GED1`

Other ovens may work if they expose the same private media routes.

## Development

Static checks:

```bash
python3 -m compileall custom_components/homeconnect_oven_private
python3 -m json.tool custom_components/homeconnect_oven_private/manifest.json >/dev/null
python3 -m json.tool custom_components/homeconnect_oven_private/strings.json >/dev/null
python3 -m json.tool custom_components/homeconnect_oven_private/translations/en.json >/dev/null
python3 -m json.tool custom_components/homeconnect_oven_private/translations/nl.json >/dev/null
python3 -m json.tool hacs.json >/dev/null
git diff --check
```

Probe script:

```bash
python3 tools/probe_private_oven_capabilities.py \
  --token-json /path/to/mobile_token.json \
  --ha-id 386020390274000097 \
  --output logs/private_oven_capabilities.json
```
