"""Minimal Home Assistant stub so the integration imports without HA installed.

The CI unit-test job installs only pytest and aiohttp, so the integration's
`homeassistant.*` imports are satisfied here with just the surface the modules
touch. Shared by the pair-power test modules.

`load()` deletes any previously imported copy of both `homeassistant*` and the
target package first, so each caller gets a clean import even when several test
modules load the integration under different package names.
"""

import importlib.util
import pathlib
import sys
import types


def load(root, pkgname):
    """Import the integration package with a minimal Home Assistant stub."""
    for key in [k for k in sys.modules if k.startswith(("homeassistant", pkgname))]:
        del sys.modules[key]

    def mod(name, **kw):
        module = types.ModuleType(name)
        module.__dict__.update(kw)
        sys.modules[name] = module
        return module

    mod("homeassistant")

    class ConfigEntry:
        runtime_data: object

        def __class_getitem__(cls, item):
            return cls

    class ConfigEntryAuthFailed(Exception):
        pass

    class ConfigEntryNotReady(Exception):
        pass

    class HomeAssistantError(Exception):
        pass

    mod("homeassistant.config_entries", ConfigEntry=ConfigEntry)
    mod(
        "homeassistant.const",
        CONF_ACCESS_TOKEN="access_token",
        UnitOfEnergy=types.SimpleNamespace(KILO_WATT_HOUR="kWh"),
        UnitOfPower=types.SimpleNamespace(WATT="W"),
    )
    mod("homeassistant.core", HomeAssistant=object, callback=lambda f: f)
    mod(
        "homeassistant.exceptions",
        ConfigEntryAuthFailed=ConfigEntryAuthFailed,
        ConfigEntryNotReady=ConfigEntryNotReady,
        HomeAssistantError=HomeAssistantError,
    )

    mod("homeassistant.components")
    mod(
        "homeassistant.components.sensor",
        SensorDeviceClass=types.SimpleNamespace(POWER="power", ENERGY="energy"),
        SensorStateClass=types.SimpleNamespace(
            MEASUREMENT="measurement", TOTAL_INCREASING="total_increasing"
        ),
        SensorEntity=type("SensorEntity", (object,), {}),
    )

    mod("homeassistant.helpers")

    class UpdateFailed(Exception):
        pass

    class DataUpdateCoordinator:
        def __init__(self, hass, logger, name=None, update_interval=None):
            self.hass = hass
            self.logger = logger
            self.name = name
            self.update_interval = update_interval
            self.data = None
            self._listeners = []

        def __class_getitem__(cls, item):
            return cls

        def async_update_listeners(self):
            pass

        def async_set_updated_data(self, data):
            self.data = data

        async def async_config_entry_first_refresh(self):
            self.data = await self._async_update_data()

    class CoordinatorEntity:
        def __init__(self, coordinator):
            self.coordinator = coordinator

        def __class_getitem__(cls, item):
            return cls

        def async_write_ha_state(self):
            pass

    mod(
        "homeassistant.helpers.update_coordinator",
        UpdateFailed=UpdateFailed,
        DataUpdateCoordinator=DataUpdateCoordinator,
        CoordinatorEntity=CoordinatorEntity,
    )
    mod("homeassistant.helpers.device_registry", DeviceInfo=lambda **kw: kw)
    mod("homeassistant.helpers.entity_platform", AddEntitiesCallback=object)

    class RestoreEntity:
        async def async_added_to_hass(self):
            pass

        async def async_get_last_state(self):
            return None

    mod("homeassistant.helpers.restore_state", RestoreEntity=RestoreEntity)
    mod(
        "homeassistant.helpers.aiohttp_client",
        async_get_clientsession=lambda hass: hass.session,
    )

    pkgdir = pathlib.Path(root) / "custom_components" / "lumin"
    spec = importlib.util.spec_from_file_location(
        pkgname, pkgdir / "__init__.py", submodule_search_locations=[str(pkgdir)]
    )
    package = importlib.util.module_from_spec(spec)
    sys.modules[pkgname] = package
    spec.loader.exec_module(package)
    return package
