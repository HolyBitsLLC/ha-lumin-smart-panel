"""Token refresh paths in async_setup_entry and LuminApiClient.

Loads the real ``__init__``, ``api`` and ``coordinator`` modules with Home Assistant stubbed, so it runs in the
unit-test job without an HA install. Covers:
- a restart with an expired access token: the refresh callback must not read ``entry.runtime_data`` before setup
  assigns it (HA declares it as an annotation only), or setup fails with AttributeError and is not retried;
- Auth0 unreachable near expiry: the poll still asks the local panel, and warns once rather than every poll;
- a dead refresh token still reaches reauth (ConfigEntryAuthFailed);
- a refresh response without ``expires_in`` takes the expiry from the new token's ``exp``.
"""
import asyncio
import base64
import importlib.util
import json
import pathlib
import sys
import time
import types

import aiohttp


def jwt(exp):
    b = lambda o: base64.urlsafe_b64encode(json.dumps(o).encode()).rstrip(b"=").decode()
    return f"{b({'alg':'RS256'})}.{b({'sub':'t','exp':exp,'azp':'eKr5fjYckpRJbP3iqywCZXapDZbB3Kgs'})}.sig"

def load(root, pkgname):
    for k in [k for k in sys.modules if k.startswith(("homeassistant", pkgname))]:
        del sys.modules[k]
    def mod(name, **kw):
        m = types.ModuleType(name); m.__dict__.update(kw); sys.modules[name] = m; return m
    mod("homeassistant")
    class ConfigEntry:            # like HA core: runtime_data is an annotation only
        runtime_data: object
        def __class_getitem__(cls, item): return cls
    mod("homeassistant.config_entries", ConfigEntry=ConfigEntry)
    mod("homeassistant.const", CONF_ACCESS_TOKEN="access_token")
    mod("homeassistant.core", HomeAssistant=object, callback=lambda f: f)
    class ConfigEntryAuthFailed(Exception): pass
    class ConfigEntryNotReady(Exception): pass
    mod("homeassistant.exceptions", ConfigEntryAuthFailed=ConfigEntryAuthFailed,
        ConfigEntryNotReady=ConfigEntryNotReady, HomeAssistantError=type("HomeAssistantError",(Exception,),{}))
    mod("homeassistant.helpers")
    mod("homeassistant.helpers.aiohttp_client", async_get_clientsession=lambda hass: hass.session)
    class UpdateFailed(Exception): pass
    class DUC:
        def __init__(self, hass, logger, name=None, update_interval=None):
            self.hass, self.data, self._listeners = hass, None, []
        def __class_getitem__(cls, item): return cls
        def async_update_listeners(self): pass
        async def async_config_entry_first_refresh(self):
            self.data = await self._async_update_data()
    mod("homeassistant.helpers.update_coordinator", UpdateFailed=UpdateFailed, DataUpdateCoordinator=DUC)
    pkgdir = pathlib.Path(root) / "custom_components" / "lumin"
    spec = importlib.util.spec_from_file_location(
        pkgname, pkgdir / "__init__.py", submodule_search_locations=[str(pkgdir)]
    )
    pkg = importlib.util.module_from_spec(spec)
    sys.modules[pkgname] = pkg
    spec.loader.exec_module(pkg)
    return pkg, ConfigEntry

class Resp:
    def __init__(s, status, body): s.status, s._b = status, body
    async def text(s): return s._b
    async def json(s): return json.loads(s._b)
    async def __aenter__(s): return s
    async def __aexit__(s, *a): return False

class Session:
    def __init__(s, post): s._post = post; s.posts = 0
    def post(s, url, json=None, timeout=None):
        s.posts += 1
        r = s._post()
        if isinstance(r, Exception): raise r
        return r

class Hass:
    def __init__(s, session):
        s.session = session
        s.config_entries = types.SimpleNamespace(async_update_entry=s._upd, async_forward_entry_setups=s._fwd)
    def _upd(s, entry, data): entry.data = data
    async def _fwd(s, entry, platforms): pass

GOOD = lambda: Resp(200, json.dumps({"access_token": jwt(time.time()+86400), "refresh_token": "rotated", "expires_in": 86400}))


ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_restart_with_expired_token_refreshes_and_sets_up():
    pkg, ConfigEntry = load(ROOT, "lumin_t_startup")
    entry = ConfigEntry()
    entry.data = {"access_token": jwt(time.time() - 60), "refresh_token": "rt", "use_cloud_fallback": True,
                  "panels": [{"panel_guid": "g", "panel_ip": "", "lsp_id": 1, "panel_name": "p"}]}
    hass = Hass(Session(GOOD))
    api = sys.modules["lumin_t_startup.api"]
    coord = sys.modules["lumin_t_startup.coordinator"]

    async def cloud_circuits(self, lsp_id):
        return [{"id": 1, "num": 1}]

    async def no_ws(self):
        pass

    api.LuminCloudClient.get_circuits = cloud_circuits
    coord.LuminDataCoordinator.async_setup_websockets = no_ws
    assert asyncio.run(pkg.async_setup_entry(hass, entry)) is True
    assert hass.session.posts == 1
    assert entry.data["refresh_token"] == "rotated"
    assert entry.data["token_expires_at"] > time.time()


def test_auth0_unreachable_near_expiry_still_polls_local_panel():
    load(ROOT, "lumin_t_offline")
    api = sys.modules["lumin_t_offline.api"]
    coord = sys.modules["lumin_t_offline.coordinator"]
    sess = Session(lambda: aiohttp.ClientConnectionError("auth0 unreachable"))
    tm = api.LuminTokenManager(session=sess, access_token=jwt(time.time() + 60), refresh_token="rt",
                               auth0_domain="x", client_id="c")
    panel = api.LuminPanel("192.0.2.1", "g", "p", 1)
    client = api.LuminApiClient(sess, tm.access_token, [panel], True, tm)
    calls = {"local": 0}

    async def local_circuits(self):
        calls["local"] += 1
        return [{"id": 1, "num": 1}]

    api.LuminLocalClient.get_circuits = local_circuits
    c = coord.LuminDataCoordinator(Hass(sess), client)
    panel.circuits = [{"id": 1, "num": 1}]
    c.data = {"panels": {"g": {"circuits": panel.circuits, "available": True}}}
    data = asyncio.run(c._async_update_data())
    assert sess.posts >= 1, "the proactive refresh was attempted"
    assert calls["local"] == 1
    assert data["panels"]["g"]["available"] is True


def _offline_coordinator(name, post, access_token):
    load(ROOT, name)
    api = sys.modules[name + ".api"]
    coord = sys.modules[name + ".coordinator"]
    sess = Session(post)
    tm = api.LuminTokenManager(session=sess, access_token=access_token, refresh_token="rt",
                               auth0_domain="x", client_id="c")
    panel = api.LuminPanel("192.0.2.1", "g", "p", 1)
    client = api.LuminApiClient(sess, tm.access_token, [panel], True, tm)
    c = coord.LuminDataCoordinator(Hass(sess), client)
    panel.circuits = [{"id": 1, "num": 1}]
    c.data = {"panels": {"g": {"circuits": panel.circuits, "available": True}}}
    return api, c, tm


def test_dead_grant_still_reaches_reauth():
    dead = lambda: Resp(403, json.dumps({"error": "invalid_grant", "error_description": "Unknown or invalid refresh token."}))
    _api, c, _ = _offline_coordinator("lumin_t_dead", dead, jwt(time.time() + 60))
    exc = sys.modules["homeassistant.exceptions"]
    try:
        asyncio.run(c._async_update_data())
    except exc.ConfigEntryAuthFailed:
        return
    raise AssertionError("a dead refresh token must raise ConfigEntryAuthFailed")


def test_outage_warns_once_not_every_poll(caplog):
    import logging
    api, c, _ = _offline_coordinator("lumin_t_warn", lambda: aiohttp.ClientConnectionError("down"),
                                     jwt(time.time() + 60))

    async def local_circuits(self):
        return [{"id": 1, "num": 1}]

    api.LuminLocalClient.get_circuits = local_circuits
    with caplog.at_level(logging.DEBUG, logger="lumin_t_warn.api"):
        for _ in range(3):
            asyncio.run(c._async_update_data())
    warns = [r for r in caplog.records if r.levelno == logging.WARNING and "Proactive token refresh" in r.getMessage()]
    assert len(warns) == 1


def test_refresh_without_expires_in_uses_new_token_exp():
    new_exp = int(time.time()) + 86400
    no_expiry = lambda: Resp(200, json.dumps({"access_token": jwt(new_exp), "refresh_token": "rotated"}))
    load(ROOT, "lumin_t_noexp")
    api = sys.modules["lumin_t_noexp.api"]
    tm = api.LuminTokenManager(session=Session(no_expiry), access_token=jwt(time.time() - 60), refresh_token="rt",
                               auth0_domain="x", client_id="c")
    assert tm.token_expired
    asyncio.run(tm.async_refresh())
    assert tm.expires_at == new_exp
    assert not tm.token_expired
