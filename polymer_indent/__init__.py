"""polymer_indent — main controller for the PEGDA UV-cure / indentation workcell.

The controller runs the per-well experiment loop: Opentrons fill -> arm transfer
-> SHARC UV cure -> arm transfer -> ASMI indentation -> bookkeeping -> arm
return. It owns the frozen cubos-format gantry/deck YAMLs and the base protocol
YAMLs; per iteration it swaps the well id into the base protocol and sends
{gantry_config, deck_config, protocol_yaml} over HTTP to the station Pi, which
runs them through its local cubos install.

This package has no cubos dependency. Only ``station_worker`` (run on each Pi)
imports cubos.
"""

__version__ = "0.1.0"
