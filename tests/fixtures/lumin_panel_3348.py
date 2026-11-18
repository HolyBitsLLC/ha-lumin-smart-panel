"""Real captured circuit metadata and readings for one Lumin panel.

PROVENANCE
==========
SOURCE 1 - circuit metadata: `/v2/lsps/3348/circuits`, served by the panel on
  its UNAUTHENTICATED HTTP port :8085, captured 2026-11-17. The authenticated
  HTTPS :443 REST API that the integration actually polls answers 401
  `failed to find a valid token in any location of the request` without an
  access token, so that path AND the cloud API remain UNCAPTURED. This fixture
  therefore pins the payload SCHEMA and the aggregation math, not the
  authenticated transport.
SOURCE 2 - readings: the panel's local WebSocket stream (:8085/ws), 40 messages
  captured 2026-11-16/17. `READINGS` is the merged snapshot: the last value seen
  for each circuit id across those messages.
PANEL   - lsp_id 3348, 14 circuit objects.

WHAT IS PRESERVED VERBATIM
==========================
Every field the aggregation reads is exactly as captured: circuit `id`, `pair`
and `pair.id`, `multiplier`, `hidden`, `main`, `num`, `name`, `type_id`, and
every reading at full float precision. Nothing was rounded, renamed or
reordered. Two MAC-derived `guid` fields were replaced with a placeholder -
`line.guid` on every circuit and `pair.guid` on the two paired circuits - because
they are hardware identifiers that no formula reads.

THE KEY OBSERVATION
===================
14 circuit objects, but the WebSocket streams 16 reading ids. The two extra ids
are exactly the `pair` targets and have NO circuit object of their own:
`3633764` (paired by main `3633763`) and `3633766` (paired by `3633765`). A
two-pole load is reported as one leg per circuit and the panel reunites the
halves through `pair`, so an integration that ignores `pair` sees each leg as
unrelated load and shows half the true draw. See `PAIR_LEG_IDS`.

GOLDEN VALUES
=============
`GOLDEN` holds the four figures Lumin's own formulas produce from this exact
snapshot. They are golden values for THIS FROZEN SNAPSHOT of ONE REAL PANEL, not
universal constants: they pin the formula so a refactor cannot silently drift
back to double-counting, and they say nothing about any other installation.
Assert them with a small tolerance.
"""

PANEL_LSP_ID = 3348
CAPTURED_AT = "2026-11-17"

CIRCUITS = [
    {'id': 3633751, 'lsp_id': 0, 'num': 1, 'name': 'Line 1', 'line': {'id': 3633751, 'num': 1, 'guid': '<redacted-mac>'}, 'pair': None, 'schedules': None, 'type_id': 100, 'phase': -1, 'index': 1, 'controllable': True, 'main': False, 'protected': False, 'power_on': True, 'manual_on': True, 'multiplier': -1, 'color': '#c7738f', 'active': False, 'max_power': 0, 'locked': False, 'hidden': False, 'metadata': None, 'spm_monitor': True, 'spm_monitor_idx': 5, 'spm_control_pref': 'essential', 'spm_ctl_idx': 13, 'sem_shed_threshold': None},
    {'id': 3633752, 'lsp_id': 0, 'num': 2, 'name': 'Line 2', 'line': {'id': 3633752, 'num': 2, 'guid': '<redacted-mac>'}, 'pair': None, 'schedules': None, 'type_id': 100, 'phase': -1, 'index': 2, 'controllable': True, 'main': False, 'protected': False, 'power_on': True, 'manual_on': True, 'multiplier': -1, 'color': '#e850e5', 'active': False, 'max_power': 0, 'locked': False, 'hidden': False, 'metadata': None, 'spm_monitor': True, 'spm_monitor_idx': 6, 'spm_control_pref': 'essential', 'spm_ctl_idx': 12, 'sem_shed_threshold': None},
    {'id': 3633753, 'lsp_id': 0, 'num': 3, 'name': 'Line 3', 'line': {'id': 3633753, 'num': 3, 'guid': '<redacted-mac>'}, 'pair': None, 'schedules': None, 'type_id': 100, 'phase': 1, 'index': 3, 'controllable': True, 'main': False, 'protected': False, 'power_on': True, 'manual_on': True, 'multiplier': -1, 'color': '#80f6e2', 'active': False, 'max_power': 0, 'locked': False, 'hidden': False, 'metadata': None, 'spm_monitor': True, 'spm_monitor_idx': 1, 'spm_control_pref': 'essential', 'spm_ctl_idx': 11, 'sem_shed_threshold': None},
    {'id': 3633754, 'lsp_id': 0, 'num': 4, 'name': 'Line 4', 'line': {'id': 3633754, 'num': 4, 'guid': '<redacted-mac>'}, 'pair': None, 'schedules': None, 'type_id': 100, 'phase': 1, 'index': 4, 'controllable': True, 'main': False, 'protected': False, 'power_on': True, 'manual_on': True, 'multiplier': -1, 'color': '#b7e1cf', 'active': False, 'max_power': 0, 'locked': False, 'hidden': False, 'metadata': None, 'spm_monitor': True, 'spm_monitor_idx': 4, 'spm_control_pref': 'essential', 'spm_ctl_idx': 10, 'sem_shed_threshold': None},
    {'id': 3633755, 'lsp_id': 0, 'num': 5, 'name': 'Downstairs Kitchenette', 'line': {'id': 3633755, 'num': 5, 'guid': '<redacted-mac>'}, 'pair': None, 'schedules': None, 'type_id': 100, 'phase': 0, 'index': 5, 'controllable': True, 'main': False, 'protected': False, 'power_on': True, 'manual_on': True, 'multiplier': -1, 'color': '#8676bb', 'active': False, 'max_power': 0, 'locked': False, 'hidden': False, 'metadata': None, 'spm_monitor': False, 'spm_monitor_idx': 0, 'spm_control_pref': 'essential', 'spm_ctl_idx': 9, 'sem_shed_threshold': None},
    {'id': 3633756, 'lsp_id': 0, 'num': 6, 'name': 'Line 6', 'line': {'id': 3633756, 'num': 6, 'guid': '<redacted-mac>'}, 'pair': None, 'schedules': None, 'type_id': 100, 'phase': -1, 'index': 6, 'controllable': True, 'main': False, 'protected': False, 'power_on': True, 'manual_on': True, 'multiplier': -1, 'color': '#50dd35', 'active': False, 'max_power': 0, 'locked': False, 'hidden': False, 'metadata': None, 'spm_monitor': True, 'spm_monitor_idx': 3, 'spm_control_pref': 'essential', 'spm_ctl_idx': 8, 'sem_shed_threshold': None},
    {'id': 3633757, 'lsp_id': 0, 'num': 7, 'name': 'Line 7', 'line': {'id': 3633757, 'num': 7, 'guid': '<redacted-mac>'}, 'pair': None, 'schedules': None, 'type_id': 100, 'phase': -1, 'index': 7, 'controllable': True, 'main': False, 'protected': False, 'power_on': True, 'manual_on': True, 'multiplier': -1, 'color': '#d53bcb', 'active': False, 'max_power': 0, 'locked': False, 'hidden': False, 'metadata': None, 'spm_monitor': True, 'spm_monitor_idx': 7, 'spm_control_pref': 'essential', 'spm_ctl_idx': 7, 'sem_shed_threshold': None},
    {'id': 3633758, 'lsp_id': 0, 'num': 8, 'name': 'Line 8', 'line': {'id': 3633758, 'num': 8, 'guid': '<redacted-mac>'}, 'pair': None, 'schedules': None, 'type_id': 100, 'phase': -1, 'index': 8, 'controllable': True, 'main': False, 'protected': False, 'power_on': True, 'manual_on': True, 'multiplier': -1, 'color': '#f567d8', 'active': False, 'max_power': 0, 'locked': False, 'hidden': False, 'metadata': None, 'spm_monitor': True, 'spm_monitor_idx': 2, 'spm_control_pref': 'essential', 'spm_ctl_idx': 6, 'sem_shed_threshold': None},
    {'id': 3633759, 'lsp_id': 0, 'num': 9, 'name': 'Line 9', 'line': {'id': 3633759, 'num': 9, 'guid': '<redacted-mac>'}, 'pair': None, 'schedules': None, 'type_id': 100, 'phase': -1, 'index': 9, 'controllable': True, 'main': False, 'protected': False, 'power_on': True, 'manual_on': True, 'multiplier': -1, 'color': '#a94949', 'active': False, 'max_power': 0, 'locked': False, 'hidden': False, 'metadata': None, 'spm_monitor': True, 'spm_monitor_idx': 8, 'spm_control_pref': 'essential', 'spm_ctl_idx': 5, 'sem_shed_threshold': None},
    {'id': 3633760, 'lsp_id': 0, 'num': 10, 'name': 'Line 10', 'line': {'id': 3633760, 'num': 10, 'guid': '<redacted-mac>'}, 'pair': None, 'schedules': None, 'type_id': 100, 'phase': 0, 'index': 10, 'controllable': True, 'main': False, 'protected': False, 'power_on': True, 'manual_on': True, 'multiplier': -1, 'color': '#8f535a', 'active': False, 'max_power': 0, 'locked': False, 'hidden': True, 'metadata': None, 'spm_monitor': True, 'spm_monitor_idx': 9, 'spm_control_pref': 'essential', 'spm_ctl_idx': 4, 'sem_shed_threshold': None},
    {'id': 3633761, 'lsp_id': 0, 'num': 11, 'name': 'Line 11', 'line': {'id': 3633761, 'num': 11, 'guid': '<redacted-mac>'}, 'pair': None, 'schedules': None, 'type_id': 100, 'phase': -1, 'index': 11, 'controllable': True, 'main': False, 'protected': False, 'power_on': True, 'manual_on': True, 'multiplier': -1, 'color': '#635159', 'active': False, 'max_power': 0, 'locked': False, 'hidden': False, 'metadata': None, 'spm_monitor': True, 'spm_monitor_idx': 10, 'spm_control_pref': 'essential', 'spm_ctl_idx': 3, 'sem_shed_threshold': None},
    {'id': 3633762, 'lsp_id': 0, 'num': 12, 'name': 'Line 12', 'line': {'id': 3633762, 'num': 12, 'guid': '<redacted-mac>'}, 'pair': None, 'schedules': None, 'type_id': 100, 'phase': 0, 'index': 12, 'controllable': True, 'main': False, 'protected': False, 'power_on': True, 'manual_on': True, 'multiplier': -1, 'color': '#b4f3c6', 'active': False, 'max_power': 0, 'locked': False, 'hidden': True, 'metadata': None, 'spm_monitor': True, 'spm_monitor_idx': 11, 'spm_control_pref': 'essential', 'spm_ctl_idx': 2, 'sem_shed_threshold': None},
    {'id': 3633763, 'lsp_id': 0, 'num': 13, 'name': 'Everything Else Sub', 'line': {'id': 3633763, 'num': 13, 'guid': '<redacted-mac>'}, 'pair': {'id': 3633764, 'num': 14, 'guid': '<redacted-mac>'}, 'schedules': None, 'type_id': 100, 'phase': 0, 'index': 79, 'controllable': False, 'main': True, 'protected': False, 'power_on': True, 'manual_on': True, 'multiplier': 1, 'color': '#dcfce8', 'active': False, 'max_power': 0, 'locked': False, 'hidden': False, 'metadata': None, 'spm_monitor': True, 'spm_monitor_idx': 0, 'spm_control_pref': 'essential', 'spm_ctl_idx': 0, 'sem_shed_threshold': None},
    {'id': 3633765, 'lsp_id': 0, 'num': 15, 'name': 'Auxiliary CT', 'line': {'id': 3633765, 'num': 15, 'guid': '<redacted-mac>'}, 'pair': {'id': 3633766, 'num': 16, 'guid': '<redacted-mac>'}, 'schedules': None, 'type_id': 100, 'phase': 0, 'index': 81, 'controllable': False, 'main': False, 'protected': False, 'power_on': True, 'manual_on': True, 'multiplier': -1, 'color': '#6b69e7', 'active': False, 'max_power': 0, 'locked': False, 'hidden': True, 'metadata': None, 'spm_monitor': False, 'spm_monitor_idx': 0, 'spm_control_pref': 'essential', 'spm_ctl_idx': 1, 'sem_shed_threshold': None},
]

CIRCUIT_IDS = [circuit["id"] for circuit in CIRCUITS]

# Streamed reading ids that have no circuit object of their own.
PAIR_LEG_IDS = [
    circuit["pair"]["id"] for circuit in CIRCUITS if circuit.get("pair")
]

# Merged WS snapshot: last reading seen per circuit id. 16 ids for 14
# circuits - the extra two are PAIR_LEG_IDS, which have no circuit object.
READINGS = {
    3633751: {
        "voltage": 124.08104,
        "current": 0.8334961,
        "power": 59.674316,
        "power_factor": 0.5770048,
    },
    3633752: {
        "voltage": 124.07805,
        "current": 0.012207031,
        "power": 1.5761719,
        "power_factor": 1.0407428,
    },
    3633753: {
        "voltage": 124.696266,
        "current": 0.01928711,
        "power": 1.3168945,
        "power_factor": 0.55719733,
    },
    3633754: {
        "voltage": 124.69833,
        "current": 0.046142578,
        "power": -0.29541016,
        "power_factor": -0.05220522,
    },
    3633755: {
        "voltage": 124.08104,
        "current": 1.4213867,
        "power": 31.36084,
        "power_factor": 0.17780851,
    },
    3633756: {
        "voltage": 124.08104,
        "current": 0.03540039,
        "power": 0.18310547,
        "power_factor": 0.043346927,
    },
    3633757: {
        "voltage": 124.091736,
        "current": 0.0071614585,
        "power": 0.05859375,
        "power_factor": 0.068520874,
    },
    3633758: {
        "voltage": 124.08981,
        "current": 0.030639648,
        "power": 0.17041016,
        "power_factor": 0.04424104,
    },
    3633759: {
        "voltage": 0,
        "current": 0,
        "power": 0,
        "power_factor": 0,
    },
    3633760: {
        "voltage": 124.08981,
        "current": 0.011230469,
        "power": 0.07739258,
        "power_factor": 0.11470531,
    },
    3633761: {
        "voltage": 124.08981,
        "current": 0.0007324219,
        "power": -0.11816406,
        "power_factor": -1.3538656,
    },
    3633762: {
        "voltage": 124.091736,
        "current": 0.020345053,
        "power": 0.1673177,
        "power_factor": 0.05730781,
    },
    3633763: {
        "voltage": 124.08104,
        "current": 8.359375,
        "power": 849.59375,
        "power_factor": 0.819082,
    },
    3633764: {
        "voltage": 124.69833,
        "current": 2.34375,
        "power": 213.84375,
        "power_factor": 0.7317203,
    },
    3633765: {
        "voltage": 124.08104,
        "current": 0,
        "power": 0,
        "power_factor": 0,
    },
    3633766: {
        "voltage": 124.69833,
        "current": 0,
        "power": 0,
        "power_factor": 0,
    },
}


def live_power():
    """The coordinator's `_live_power` map: circuit id string -> watts."""
    return {str(cid): reading["power"] for cid, reading in READINGS.items()}


GOLDEN = {
    # getGridPower: the main circuit plus its paired leg. The main meter
    # already covers everything downstream, so this is the true whole-panel
    # draw and what the panel total must report.
    "grid_power": 1063.44,
    # getEverythingElse: sum of multiplier * (own + pair) over visible
    # circuits with a truthy multiplier. This is what a main circuit's own
    # tile shows in Lumin's app.
    "everything_else": 969.51,
    # What the integration reported before this fix: the plain sum of the 14
    # circuit objects, low by the main meter's paired leg.
    "legacy_total": 943.77,
    # What a pair fix WITHOUT the total correction would report: every
    # circuit plus its pair, so the main's leg is counted twice.
    "naive_total": 1157.61,
}


def legacy_total():
    """Sum of the circuit objects only - the pre-fix panel total."""
    return sum(READINGS[circuit_id]["power"] for circuit_id in CIRCUIT_IDS)


def naive_total():
    """Every circuit plus its pair, with no dedup - the double-counting bug."""
    total = 0.0
    for circuit in CIRCUITS:
        total += READINGS[circuit["id"]]["power"]
        if circuit.get("pair"):
            total += READINGS[circuit["pair"]["id"]]["power"]
    return total