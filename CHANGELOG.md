# Changelog

## 0.1.0 (2026-10-31)


### Features

* add energy sensors for HA Energy dashboard support ([d606ace](https://github.com/HolyBitsLLC/ha-lumin-smart-panel/commit/d606ace63103811ac650cd8589aeb18fdc27b2c9))
* add release-please for automated versioning and HACS upgrades ([3d105a6](https://github.com/HolyBitsLLC/ha-lumin-smart-panel/commit/3d105a65f88784cca406db81fddf41e1a75ec68b))
* auto-refresh Auth0 tokens to prevent expiry ([0e9c0b2](https://github.com/HolyBitsLLC/ha-lumin-smart-panel/commit/0e9c0b23db853b71a74b04fa50823d4a4d860088))
* **image:** build the Harbor payload image in this repository on the self-hosted runners ([4da6430](https://github.com/HolyBitsLLC/ha-lumin-smart-panel/commit/4da6430487c9052b0699f776faf58bc872abf431))
* **image:** pin the inlined Kaniko mirror and guard it against drift ([c0a306d](https://github.com/HolyBitsLLC/ha-lumin-smart-panel/commit/c0a306daf9d4319a5ff161d8f6e1b9f5ca78f9ae))
* Lumin Smart Panel custom integration for Home Assistant ([0731510](https://github.com/HolyBitsLLC/ha-lumin-smart-panel/commit/07315103dd768a37a1737a818a496f104c3b270f))
* single-field token input — paste full JSON blob from Local Storage ([bfc895f](https://github.com/HolyBitsLLC/ha-lumin-smart-panel/commit/bfc895fb925aa2b4fe0f2b98b4f4bc1b92813f2a))


### Bug Fixes

* add diagnostic logging and catch LuminConnectionError in config flow ([f7d141d](https://github.com/HolyBitsLLC/ha-lumin-smart-panel/commit/f7d141dc19fb325495c44fbddd41114491886489))
* avoid blocking ssl.create_default_context() in event loop ([6e82f1d](https://github.com/HolyBitsLLC/ha-lumin-smart-panel/commit/6e82f1ddba92dc19fce4c95a96808f75b9995d8b))
* **ci:** green the lint and test jobs on master's inherited red ([e7730b2](https://github.com/HolyBitsLLC/ha-lumin-smart-panel/commit/e7730b2eed7db65ad7036b0c9197b837f87e2498))
* handle truncated JSON in token parser with regex fallback ([a218710](https://github.com/HolyBitsLLC/ha-lumin-smart-panel/commit/a218710befba646dbe8dbc9a789524b2fa5854ec))
* **image:** keep the mirror guard ruff-clean ([201bbbd](https://github.com/HolyBitsLLC/ha-lumin-smart-panel/commit/201bbbd4d409dd2e57e790605b06b6447a3c1193))
* **image:** keep the mirror guard runnable on the self-hosted python3 ([35b86ea](https://github.com/HolyBitsLLC/ha-lumin-smart-panel/commit/35b86ead966cf9f92e708be50bf7fa43e58baedc))
* use SelectSelector for panel selection form ([18a336c](https://github.com/HolyBitsLLC/ha-lumin-smart-panel/commit/18a336c23f0b0c9571c7151f6a08176b2d871f43))
* use TextSelector multiline for token_data field ([8d84ea4](https://github.com/HolyBitsLLC/ha-lumin-smart-panel/commit/8d84ea439cb18a3da0d3e86c9384d65677d47024))
