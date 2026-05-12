"""xArm + Vention-rail IPs and named poses for the BEAR-DEN workcell.

Copied verbatim from denos (workers/arm_rail_worker/arm_worker.py, which credits
orchestrate_uv_asmi_loop.py). Edit here if the deck / fixtures move.

Each arm pose is [x, y, z, roll, pitch, yaw] in the xArm base frame (mm / deg).
"""

# --- hardware endpoints -----------------------------------------------------
ARM_IP = "10.210.29.16"          # bear-den-arm1 (Ufactory xArm Lite 6)
RAIL_IP = "10.210.29.15"         # bear-den-vention (Vention rail)
ARM_SPEED = 50                   # default arm speed (mm/s)
RAIL_TIMEOUT = 15                # rail move timeout (s)

# --- arm poses --------------------------------------------------------------
ARM_SAFE_POSITION = [0, 150, 200, 180, 0, 0]

# UV-curing station
UV_PICKUP_POSITION = [253, 188, 99, 180, 0, 0]
UV_PICKUP_LIFTED = [253, 188, 200, 180, 0, 0]
UV_RAIL_POSITION_MM = 600

# ASMI station (slide-in / slide-out tray) — re-measured 2026-05-12 (was [272/369, 60.5, ...])
ASMI_SLIDE_IN_POSITION = [280.5, 35, 33.5, 180, 0, 90]
ASMI_SLIDE_IN_LIFTED = [280.5, 35, 200, 180, 0, 90]
ASMI_SLIDE_OUT_POSITION = [376, 35, 33.5, 180, 0, 90]
ASMI_SLIDE_OUT_LIFTED = [376, 35, 200, 180, 0, 90]
ASMI_SLIDE_IN_PUSH = [270.5, 35, 33.5, 180, 0, 90]   # TODO confirm — derived as SLIDE_IN_POSITION − 10 mm in x (seats the plate)
ASMI_RAIL_POSITION_MM = 1000

# Opentrons deck slot D1 — two plate variants; pick one.
OT_PLATE_TYPE = "black"          # "black" or "transparent"
OT_TRANSPARENT = {
    "D1_PICKUP": [313, 117, 116, -180, 0, -90],
    "D1_LIFTED": [313, 117, 200, -180, 0, -90],
}
OT_BLACK = {
    "D1_PICKUP": [312, 117, 118, -180, 0, -90],
    "D1_LIFTED": [312, 117, 200, -180, 0, -90],
}


def ot_positions(plate_type: str = OT_PLATE_TYPE):
    d = OT_TRANSPARENT if plate_type == "transparent" else OT_BLACK
    return d["D1_PICKUP"], d["D1_LIFTED"]
