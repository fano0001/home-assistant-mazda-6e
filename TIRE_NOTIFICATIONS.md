# Tire warning notifications

Tire status 1 or 2 creates a persistent notification for the affected wheel.
Status 0 removes it. Missing data or connection failures do not clear a warning.
The title uses the HA device name. Code 2 is shown as low tire pressure; code 1
keeps its raw code because its meaning is unknown. The message includes the last
reported pressure when available. No mobile push service is required.
