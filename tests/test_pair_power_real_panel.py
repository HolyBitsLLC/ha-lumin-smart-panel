"""Golden parity against a real captured Lumin panel payload.

These tests are the ones that would have caught the double-counting bug. They
drive the coordinator with the real `/v2/lsps/3348/circuits` metadata and the
real WebSocket readings snapshot from `tests/fixtures/lumin_panel_3348.py`, and
assert the figures Lumin's own formulas produce from that snapshot:

    grid (main + its pair)        1063.44 W
    everything else               969.51 W
    pre-fix total (14 objects)    943.77 W
    naive pair fix, no dedup     1157.61 W

Read the fixture's module docstring for provenance. The payloads come from the
panel's UNAUTHENTICATED :8085 port; the authenticated :443 path the integration
actually polls answers 401 without an access token, and the cloud path was not
captured. The formulas and schema are therefore what is being pinned here.

The four numbers are golden values for ONE FROZEN SNAPSHOT of ONE REAL PANEL.
They are not universal constants and they say nothing about any other
installation: they exist so a refactor cannot silently drift back to summing a
main meter together with its downstream circuits. `test_pair_power.py` keeps the
installation-independent formula and edge-case coverage.
"""

import asyncio
import importlib
import pathlib
import types

import pytest
from fixtures import lumin_panel_3348 as panel
from fixtures.ha_stub import load

GUID = "TEST-GUID"
PACKAGE = "lumin_real_panel"

TOLERANCE = 0.01


@pytest.fixture(scope="module")
def pkg():
    root = pathlib.Path(__file__).resolve().parents[1]
    return load(root, PACKAGE)


def submodule(name):
    return importlib.import_module(f"{PACKAGE}.{name}")


class _Panel:
    """Panel stand-in with only the attributes the platforms read."""

    def __init__(self, guid):
        self.guid = guid
        self.lsp_id = panel.PANEL_LSP_ID
        self.ip_address = "127.0.0.1"
        self.online = True
        self.name = "Test Panel"
        self.firmware_version = "0"
        self.circuits = []
        self.available = False


@pytest.fixture()
def coordinator(pkg):
    mod = submodule("coordinator")
    coord = mod.LuminDataCoordinator(hass=object(), client=None)
    coord.data = {
        "panels": {GUID: {"circuits": panel.CIRCUITS, "available": True}}
    }
    coord.client = types.SimpleNamespace(panels={GUID: _Panel(GUID)})
    coord._live_power = panel.live_power()
    coord._rebuild_pair_map(panel.CIRCUITS)
    return coord


# --------------------------------------------------------------------------
# The fixture itself: this is the evidence for the dropped-leg mechanism
# --------------------------------------------------------------------------


def test_fixture_has_more_readings_than_circuits():
    assert len(panel.CIRCUITS) == 14
    assert len(panel.READINGS) == 16


def test_the_extra_reading_ids_are_exactly_the_pair_targets():
    circuit_ids = {circuit["id"] for circuit in panel.CIRCUITS}
    streamed = set(panel.READINGS)

    extra = streamed - circuit_ids
    assert extra == set(panel.PAIR_LEG_IDS)

    # A pair target is streamed but has no circuit object of its own, which is
    # why ignoring `pair` leaves those readings attributed to nothing.
    for leg_id in panel.PAIR_LEG_IDS:
        assert leg_id not in circuit_ids


def test_only_two_circuits_are_paired():
    paired = [c for c in panel.CIRCUITS if c.get("pair")]
    assert len(paired) == 2
    assert len([c for c in panel.CIRCUITS if not c.get("pair")]) == 12


def test_pair_map_matches_the_payload(coordinator):
    assert coordinator._pair_of == {
        str(c["id"]): str(c["pair"]["id"])
        for c in panel.CIRCUITS
        if c.get("pair")
    }
    assert sorted(coordinator._pair_legs) == sorted(str(i) for i in panel.PAIR_LEG_IDS)


# --------------------------------------------------------------------------
# Golden parity
# --------------------------------------------------------------------------


def test_panel_total_matches_lumin_grid_power(coordinator):
    """getGridPower: main + its paired leg. The whole-panel figure."""
    assert coordinator.get_panel_power(GUID) == pytest.approx(
        panel.GOLDEN["grid_power"], abs=TOLERANCE
    )


def test_everything_else_matches_lumin(coordinator):
    """getEverythingElse: multiplier * (own + pair), hidden/falsy skipped."""
    assert coordinator.get_everything_else(GUID) == pytest.approx(
        panel.GOLDEN["everything_else"], abs=TOLERANCE
    )


def test_golden_legacy_total_is_what_the_old_code_reported(coordinator):
    """The pre-fix total, recomputed from the fixture, is the recorded value."""
    assert panel.legacy_total() == pytest.approx(
        panel.GOLDEN["legacy_total"], abs=TOLERANCE
    )
    # And the fixed total is deliberately NOT that number.
    assert coordinator.get_panel_power(GUID) != pytest.approx(
        panel.GOLDEN["legacy_total"], abs=TOLERANCE
    )


def test_golden_naive_total_is_the_double_count_this_fix_avoids(coordinator):
    """A pair fix that left the total alone would report the naive figure."""
    assert panel.naive_total() == pytest.approx(
        panel.GOLDEN["naive_total"], abs=TOLERANCE
    )
    # The regression this guards: summing every circuit plus its pair counts
    # the main meter's leg twice and overstates the panel by ~94 W.
    assert coordinator.get_panel_power(GUID) != pytest.approx(
        panel.GOLDEN["naive_total"], abs=TOLERANCE
    )


def test_main_tile_shows_everything_else(coordinator):
    main_ids = [c["id"] for c in panel.CIRCUITS if c.get("main")]
    assert len(main_ids) == 1

    assert coordinator.get_circuit_power(GUID, main_ids[0]) == pytest.approx(
        panel.GOLDEN["everything_else"], abs=TOLERANCE
    )


# --------------------------------------------------------------------------
# The paired legs on the real payload
# --------------------------------------------------------------------------


def test_main_circuit_sums_its_pair(coordinator):
    main_id = next(c["id"] for c in panel.CIRCUITS if c.get("main"))
    pair_id = next(
        c["pair"]["id"] for c in panel.CIRCUITS if c.get("main")
    )

    expected = panel.READINGS[main_id]["power"] + panel.READINGS[pair_id]["power"]
    assert coordinator.get_live_power(main_id) == pytest.approx(
        expected, abs=TOLERANCE
    )
    # The half-reading symptom: the main alone is well short of the pair sum.
    assert panel.READINGS[main_id]["power"] < expected


def test_unpaired_circuit_is_unchanged(coordinator):
    unpaired = next(
        c["id"] for c in panel.CIRCUITS if not c.get("pair") and not c.get("hidden")
    )
    assert coordinator.get_live_power(unpaired) == pytest.approx(
        panel.READINGS[unpaired]["power"], abs=TOLERANCE
    )


def test_hidden_paired_circuit_is_excluded_from_everything_else(coordinator):
    """A hidden circuit and its leg contribute nothing to the aggregate."""
    hidden_paired = [
        c for c in panel.CIRCUITS if c.get("hidden") and c.get("pair")
    ]
    assert hidden_paired, "fixture is expected to contain one"

    dropped = 0.0
    for circuit in hidden_paired:
        dropped += abs(panel.READINGS[circuit["id"]]["power"])
        dropped += abs(panel.READINGS[circuit["pair"]["id"]]["power"])
    # Non-zero in general, but excluded: the aggregate matches Lumin exactly.
    assert coordinator.get_everything_else(GUID) == pytest.approx(
        panel.GOLDEN["everything_else"], abs=TOLERANCE
    )


def test_multiplier_is_not_applied_to_a_circuit_reading(coordinator):
    """Every branch circuit carries a non-unit multiplier; none scales its own W."""
    branch = next(
        c
        for c in panel.CIRCUITS
        if c.get("multiplier") not in (None, 0, 1) and not c.get("pair")
    )
    assert branch["multiplier"] != 1

    value = coordinator.get_live_power(branch["id"])
    assert value == pytest.approx(panel.READINGS[branch["id"]]["power"], abs=TOLERANCE)
    assert value != pytest.approx(
        branch["multiplier"] * panel.READINGS[branch["id"]]["power"], abs=TOLERANCE
    )


# --------------------------------------------------------------------------
# Entities on the real payload
# --------------------------------------------------------------------------


def test_no_entities_are_created_for_the_real_pair_legs(pkg, coordinator):
    sensor = submodule("sensor")
    created = []
    entry = types.SimpleNamespace(runtime_data=coordinator)

    asyncio.run(sensor.async_setup_entry(object(), entry, created.extend))

    unique_ids = {entity._attr_unique_id for entity in created}
    for leg_id in panel.PAIR_LEG_IDS:
        assert f"lumin_{GUID}_{leg_id}_power" not in unique_ids
        assert f"lumin_{GUID}_{leg_id}_energy" not in unique_ids

    # Every real circuit object still gets its sensors.
    for circuit_id in panel.CIRCUIT_IDS:
        assert f"lumin_{GUID}_{circuit_id}_power" in unique_ids
