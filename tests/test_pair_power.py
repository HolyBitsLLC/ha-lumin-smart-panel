"""Pair-aware power aggregation: edge cases the real panel does not exhibit.

FIXTURE PROVENANCE — READ THIS BEFORE CHANGING A NUMBER
======================================================
The circuit metadata in this file is **hand-built and synthetic**. It was NOT
captured from a panel. Circuit ids, names, `num`, `hidden` flags, `multiplier`
values and which ids are paired are all invented, so these tests describe the
*formulas* for arrangements a single captured payload cannot demonstrate:
mutual pair edges, `pair: {}`, `pair.id == self`, a panel with no main circuit,
and an empty or failed poll.

Golden parity against a REAL captured panel lives in
`test_pair_power_real_panel.py`; that module owns the installation-specific
numbers. Nothing here depends on any installation.

The reading *shape* mirrors the panel's WebSocket stream
(`{"<circuit_id>": {"voltage": V, "current": A, "power": W, "power_factor": PF}}`,
string circuit-id keys, line-to-neutral voltage), which is why a two-pole load
arrives as one leg per circuit and is reunited through the `pair` object.

Loads the real modules with Home Assistant stubbed (see `fixtures/ha_stub.py`),
so this runs in the unit-test job without an HA install.
"""

import asyncio
import importlib
import json
import pathlib
import types

import pytest
from fixtures.ha_stub import load

GUID = "TEST-GUID"


def submodule(name):
    return importlib.import_module(f"lumin_under_test.{name}")


@pytest.fixture(scope="module")
def pkg():
    root = pathlib.Path(__file__).resolve().parents[1]
    return load(root, "lumin_under_test")


@pytest.fixture()
def coordinator(pkg):
    mod = submodule("coordinator")
    return mod.LuminDataCoordinator(hass=object(), client=None)


class _Panel:
    """Panel stand-in with only the attributes the platforms read."""

    def __init__(self, guid):
        self.guid = guid
        self.lsp_id = 1
        self.ip_address = "127.0.0.1"
        self.online = True
        self.name = "Test Panel"
        self.firmware_version = "0"
        self.circuits = []
        self.available = False


# --------------------------------------------------------------------------
# Synthetic circuits
#
# `12` and `16` are paired legs the panel streams readings for but does NOT
# list as circuit objects. The ids are small and obviously invented; nothing
# here mirrors a real installation.
# --------------------------------------------------------------------------


def panel_with_main():
    return [
        {"id": 11, "num": 1, "name": "Circuit A", "pair": {"id": 12},
         "main": False, "hidden": False, "multiplier": 2},
        {"id": 13, "num": 3, "name": "Circuit B", "pair": None,
         "main": False, "hidden": False, "multiplier": 2},
        {"id": 14, "num": 4, "name": "Circuit C", "pair": None,
         "main": False, "hidden": False, "multiplier": None},
        {"id": 15, "num": 5, "name": "Meter", "pair": {"id": 16},
         "main": True, "hidden": False, "multiplier": 1},
        {"id": 17, "num": 7, "name": "Hidden", "pair": None,
         "main": False, "hidden": True, "multiplier": 5},
    ]


def panel_without_main():
    return [c for c in panel_with_main() if not c["main"]]


READINGS = {
    "11": 100.0,
    "12": 40.0,
    "13": 25.0,
    "14": 9.0,
    "15": 500.0,
    "16": 90.0,
    "17": 7.0,
}


def prime(coordinator, circuits, readings):
    """Give the coordinator cached circuits and live readings."""
    coordinator.data = {"panels": {GUID: {"circuits": circuits, "available": True}}}
    coordinator.client = types.SimpleNamespace(panels={GUID: _Panel(GUID)})
    coordinator._live_power = dict(readings)
    coordinator._rebuild_pair_map(circuits)
    return coordinator


# --------------------------------------------------------------------------
# 1. Pairing is discovered from the payload, never assumed
# --------------------------------------------------------------------------


def test_pair_map_is_read_from_the_payload(coordinator):
    prime(coordinator, panel_with_main(), READINGS)

    assert coordinator._pair_of == {"11": "12", "15": "16"}
    assert coordinator.is_pair_leg(12) is True
    assert coordinator.is_pair_leg(16) is True
    assert coordinator.is_pair_leg(11) is False
    assert coordinator.is_pair_leg(13) is False


def test_unpaired_payload_produces_no_pairs(coordinator):
    circuits = [dict(c, pair=None) for c in panel_with_main()]
    prime(coordinator, circuits, READINGS)

    assert coordinator._pair_of == {}
    assert coordinator._pair_legs == set()


def test_pair_object_variants_are_tolerated(coordinator):
    circuits = [
        {"id": 31, "pair": None},
        {"id": 32, "pair": {}},
        {"id": 33, "pair": {"id": None}},
        {"id": 34},
        {"id": 35, "pair": "not-a-dict"},
    ]
    prime(coordinator, circuits, {})

    assert coordinator._pair_of == {}
    assert coordinator._pair_legs == set()


# --------------------------------------------------------------------------
# 2-4. get_live_power: paired sum, unpaired passthrough, missing leg
# --------------------------------------------------------------------------


def test_paired_circuit_sums_both_legs(coordinator):
    prime(coordinator, panel_with_main(), READINGS)

    assert coordinator.get_live_power(11) == pytest.approx(100.0 + 40.0)


def test_unpaired_circuit_is_unchanged(coordinator):
    prime(coordinator, panel_with_main(), READINGS)

    assert coordinator.get_live_power(13) == pytest.approx(25.0)
    assert coordinator.get_live_power(14) == pytest.approx(9.0)


def test_missing_pair_reading_falls_back_to_own_leg(coordinator):
    readings = {k: v for k, v in READINGS.items() if k != "12"}
    prime(coordinator, panel_with_main(), readings)

    # Never None and never silently doubled: the leg simply has not reported.
    assert coordinator.get_live_power(11) == pytest.approx(100.0)


def test_circuit_without_any_reading_is_none(coordinator):
    prime(coordinator, panel_with_main(), {})

    assert coordinator.get_live_power(11) is None


def test_self_referential_pair_is_ignored(coordinator):
    prime(coordinator, [{"id": 41, "pair": {"id": 41}}], {"41": 12.0})

    assert coordinator._pair_of == {}
    assert coordinator.get_live_power(41) == pytest.approx(12.0)


def test_mutual_pair_is_counted_once(coordinator):
    """Defensive: both directions reported must not double-count.

    Also covers the shape where the paired leg IS listed as a circuit object,
    which would otherwise create two entities for one physical load.
    """
    circuits = [
        {"id": 51, "pair": {"id": 52}, "main": False},
        {"id": 52, "pair": {"id": 51}, "main": False},
    ]
    prime(coordinator, circuits, {"51": 60.0, "52": 40.0})

    assert coordinator._pair_of == {"51": "52"}
    assert coordinator.is_pair_leg(52) is True
    assert coordinator.is_pair_leg(51) is False

    assert coordinator.get_live_power(51) == pytest.approx(60.0 + 40.0)
    assert coordinator.get_panel_power(GUID) == pytest.approx(60.0 + 40.0)


# --------------------------------------------------------------------------
# 5-6. Panel total: Lumin's grid-power precedence
# --------------------------------------------------------------------------


def test_panel_total_with_main_uses_main_and_its_pair(coordinator):
    prime(coordinator, panel_with_main(), READINGS)

    # A main meter already covers everything downstream; adding the branch
    # circuits too would count most of the panel twice.
    assert coordinator.get_panel_power(GUID) == pytest.approx(500.0 + 90.0)


def test_panel_total_without_main_sums_each_logical_circuit_once(coordinator):
    readings = {k: READINGS[k] for k in ("11", "12", "13", "14", "17")}
    prime(coordinator, panel_without_main(), readings)

    # 11 counts as 100+40; its leg 12 is never added again.
    assert coordinator.get_panel_power(GUID) == pytest.approx(
        (100.0 + 40.0) + 25.0 + 9.0 + 7.0
    )


def test_panel_total_is_none_without_circuits(coordinator):
    prime(coordinator, [], {})

    assert coordinator.get_panel_power(GUID) is None


# --------------------------------------------------------------------------
# 7. "Everything Else" aggregate and the main tile
# --------------------------------------------------------------------------


def test_everything_else_applies_multiplier_and_skips_hidden_and_falsy(coordinator):
    prime(coordinator, panel_with_main(), READINGS)

    # multiplier * (own + pair) for every visible circuit with a truthy
    # multiplier. Circuit 14 has no multiplier (falsy); 17 is hidden.
    expected = (
        2 * (100.0 + 40.0)  # 11 and its leg 12
        + 2 * 25.0  # 13
        + 1 * (500.0 + 90.0)  # 15 and its leg 16
    )
    assert coordinator.get_everything_else(GUID) == pytest.approx(expected)


def test_main_tile_shows_everything_else(coordinator):
    prime(coordinator, panel_with_main(), READINGS)

    assert coordinator.get_circuit_power(GUID, 15) == pytest.approx(
        coordinator.get_everything_else(GUID)
    )
    # A normal circuit is unaffected by that rule.
    assert coordinator.get_circuit_power(GUID, 11) == pytest.approx(100.0 + 40.0)


def test_multiplier_is_never_applied_to_a_circuit_reading(coordinator):
    """`multiplier` carries the aggregate's sign, not a per-circuit scale."""
    prime(coordinator, panel_with_main(), READINGS)

    # Circuit 11 has multiplier 2 but still reports its raw pair sum.
    assert coordinator.get_circuit_power(GUID, 11) == pytest.approx(100.0 + 40.0)
    assert coordinator.get_live_power(11) == pytest.approx(100.0 + 40.0)


# --------------------------------------------------------------------------
# 8. The map tracks the panel and survives a failed/empty poll
# --------------------------------------------------------------------------


def test_pair_map_follows_the_payload(coordinator):
    prime(coordinator, panel_with_main(), READINGS)
    assert coordinator.is_pair_leg(12) is True

    unpaired = [dict(c, pair=None) for c in panel_with_main()]
    prime(coordinator, unpaired, READINGS)
    assert coordinator.is_pair_leg(12) is False

    prime(coordinator, panel_with_main(), READINGS)
    assert coordinator.is_pair_leg(12) is True


class _Client:
    def __init__(self, responses):
        self._responses = list(responses)
        self.panels = {GUID: _Panel(GUID)}

    async def get_circuits(self, panel):
        result = self._responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def test_empty_poll_does_not_wipe_the_pair_map(coordinator):
    coordinator.client = _Client([panel_with_main(), [], []])
    coordinator._live_power = dict(READINGS)

    asyncio.run(coordinator._async_update_data())
    assert coordinator.is_pair_leg(12) is True

    # An empty payload carries no pairing information, so the last known
    # pairing must be kept rather than silently un-pairing the panel.
    asyncio.run(coordinator._async_update_data())
    asyncio.run(coordinator._async_update_data())
    assert coordinator.is_pair_leg(12) is True


def test_failed_poll_keeps_pairing_from_cached_circuits(coordinator):
    error = submodule("api").LuminConnectionError("boom")
    coordinator.client = _Client([panel_with_main(), error])
    coordinator._live_power = dict(READINGS)

    asyncio.run(coordinator._async_update_data())
    assert coordinator.is_pair_leg(12) is True

    asyncio.run(coordinator._async_update_data())
    assert coordinator.is_pair_leg(12) is True


# --------------------------------------------------------------------------
# 9. WebSocket parsing drops non-numeric readings
# --------------------------------------------------------------------------


def _ws_client(pkg, sink):
    websocket = submodule("websocket")
    return websocket.LuminWebSocketClient(
        session=None,
        panel_ip=None,
        lsp_id=1,
        access_token="x",
        on_power_readings=sink,
    )


def test_websocket_ignores_non_numeric_power(pkg):
    seen = {}

    _ws_client(pkg, seen.update)._handle_message(
        json.dumps(
            {
                "type": 6,
                "event": {
                    "readings": {
                        "11": {"voltage": 124.0, "current": 0.1, "power": 42.5,
                               "power_factor": 0.9},
                        "12": {"voltage": 124.0, "current": 0.0, "power": "N/A",
                               "power_factor": 0.0},
                        "13": {"power": None},
                        "14": {"power": "17.25"},
                    }
                },
            }
        )
    )

    # "N/A" and None are dropped; numeric strings are still accepted.
    assert seen == {"11": 42.5, "14": 17.25}


def test_websocket_all_non_numeric_dispatches_nothing(pkg):
    calls = []

    _ws_client(pkg, calls.append)._handle_message(
        json.dumps({"type": 6, "event": {"readings": {"11": {"power": "N/A"}}}})
    )

    assert calls == []


# --------------------------------------------------------------------------
# 10. No entity for the second leg, so the Energy dashboard cannot double-count
# --------------------------------------------------------------------------


def _mutual_circuits():
    return [
        {"id": 61, "num": 1, "name": "Circuit A", "pair": {"id": 62},
         "main": False, "hidden": False, "multiplier": 2},
        {"id": 62, "num": 2, "name": "Circuit B", "pair": {"id": 61},
         "main": False, "hidden": False, "multiplier": 2},
    ]


def test_no_entities_are_created_for_a_pair_leg(pkg, coordinator):
    circuits = _mutual_circuits()
    prime(coordinator, circuits, {"61": 60.0, "62": 40.0})

    sensor = submodule("sensor")
    created = []
    entry = types.SimpleNamespace(runtime_data=coordinator)

    asyncio.run(sensor.async_setup_entry(object(), entry, created.extend))

    unique_ids = {entity._attr_unique_id for entity in created}
    assert f"lumin_{GUID}_61_power" in unique_ids
    assert f"lumin_{GUID}_61_energy" in unique_ids
    assert f"lumin_{GUID}_62_power" not in unique_ids
    assert f"lumin_{GUID}_62_energy" not in unique_ids


def test_power_entity_goes_unavailable_when_it_becomes_a_leg(pkg, coordinator):
    circuits = _mutual_circuits()
    prime(coordinator, circuits, {"61": 60.0, "62": 40.0})

    sensor = submodule("sensor")
    panel = _Panel(GUID)

    # Start from an unpaired panel so the entity exists, like a circuit that is
    # paired in the Lumin app while Home Assistant is running.
    coordinator._pair_of = {}
    coordinator._pair_legs = set()
    entity = sensor.LuminCircuitPowerSensor(coordinator, panel, circuits[1])
    assert entity.available is True

    coordinator._rebuild_pair_map(circuits)
    assert entity.available is False
