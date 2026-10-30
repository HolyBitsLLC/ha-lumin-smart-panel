# Delivery image for the Lumin Smart Panel Home Assistant integration.
#
# This image is a file carrier and nothing else. Home Assistant loads the
# integration from /config/custom_components/lumin inside the Home Assistant
# container, so harvester-argo's `install-lumin` init container copies
# /opt/lumin from THIS image into the HA /config volume; no process in this
# image ever runs.
#
# It is built by HolyBitsLLC/harvester-argo's `publish-lumin.yaml` (publish
# route B, ADR-0034 / ADR-0073). This repository is PUBLIC, so it cannot call
# the private reusable Kaniko workflow, and running the estate's self-hosted
# runners from a public repository was rejected as supply-chain exposure. The
# private side clones this repository at a pinned commit and pushes the result
# to harbor.dittmanfamily.com/holybitsllc/ha-lumin-smart-panel.
#
# There are deliberately no build args: the consumer identifies the payload by
# hashing the staged tree, so an image built by hand and an image built by CI
# are the same artifact and no provenance file can silently be empty.
#
# The Alpine base is deliberate: the init container's shell, `cp`, `sed`, `awk`
# and `grep` come from busybox, and an ~8 MB carrier is cheap to re-pull on
# every Home Assistant pod start.
FROM alpine:3.21

LABEL org.opencontainers.image.title="ha-lumin-smart-panel" \
      org.opencontainers.image.description="Home Assistant custom component payload for Lumin Smart Panels" \
      org.opencontainers.image.source="https://github.com/HolyBitsLLC/ha-lumin-smart-panel"

# Verbatim copy of the component. The init container re-verifies manifest.json,
# __init__.py and config_flow.py before it replaces anything on the PVC, so a
# broken build can never half-install over a working copy.
COPY custom_components/lumin/ /opt/lumin/

# Fail the build here, not after a bad image has replaced a working copy on
# someone's PVC.
RUN set -eu; \
    test -s /opt/lumin/manifest.json; \
    test -s /opt/lumin/__init__.py; \
    test -s /opt/lumin/config_flow.py

# No Python runtime and no requirements install: the integration's only
# requirement is `aiohttp>=3.8.0`, which Home Assistant already ships and pins
# as a core dependency. HA's requirement installer therefore finds it satisfied
# and installs nothing at runtime -- no /config/deps volume is needed.
