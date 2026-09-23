# Building the Camera Module

**Where this lives:** a new file under `modules/Camera Module/`, not a
new heading appended to the base station's own
[`docs/BUILD.md`](../../docs/BUILD.md). The base's `BUILD.md` is
ESP32-C3/MicroPython flashing instructions (`mpy-cross`, `mpremote`,
flash filesystem layout) - none of that applies here. This module runs
on Raspberry Pi OS with a normal Python install, a completely different
toolchain and audience. Per-module build docs live with their module
going forward, not folded into the base's - see
[`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md)'s `modules/`
layout note.

**Status check first:** only the RGBW flash subsystem is built and
tested (against stubs/mocks - see [`README.md`](README.md)'s Status
section). Capture scheduling, the light sensor driver, and MQTT don't
exist yet, so this page covers wiring/mounting the ring only - not a
full module build yet.

## Hardware

See [`README.md`](README.md#hardware--bom) for the parts list.

## Wiring

- **Data → GPIO18** (Pi physical pin 12, BCM numbering - see [`pins.py`](pins.py)). Direct 3.3V drive, **no level shifter**. WS2812B nominally wants ~4.5V+ for a reliably-read "1" bit; running it straight off the Pi's 3.3V logic is a documented risk acceptance, not a guarantee - it tends to work in practice over the short wire runs this module uses, but if you see flickering, wrong colors, or pixels dropping out, a level shifter (74AHCT125 or similar) or a shorter data line are the first things to try.
- **Power → 5V rail.** A 7-pixel WS2812B RGBW ring draws meaningfully more current at full white than the Pi Zero 2 W's own 5V rail may comfortably supply alongside the Pi and camera - if you see brownouts (Pi resets, camera glitches) when the flash fires, power the ring from a separate 5V supply and only share ground with the Pi, not the 5V line itself.
- **Ground → common ground with the Pi.** Required even if the ring is powered from a separate 5V supply (see above) - a floating/missing ground reference on the data line is a common cause of unreliable WS2812 signaling.
- A **large electrolytic capacitor (~1000µF) across the ring's 5V/GND** right at the ring is recommended (standard WS2812 practice) to smooth the inrush current when all 7 pixels switch on at once for the flash.
- A **~300-500Ω resistor in series on the data line**, close to the Pi's GPIO18 pin, is recommended (standard WS2812 practice, same reasoning as the base station's own WS2812 wiring note in its `docs/BUILD.md`) - signal protection, not brightness (brightness is set in software, see [`ring.py`](ring.py)).

## Mounting

**The ring must not glare or reflect into the camera lens.** This is a real capture-quality constraint, not a nice-to-have: a fill flash that bounces directly back into the lens (off the ring's own diffuser, a nearby reflective surface, or the enclosure itself) will blow out the shot it's supposed to be helping. Concretely:

- Mount the ring **around** the lens (a typical "ring light" placement) rather than off to one side pointed across the lens's field of view - off-axis placement is far more likely to catch a reflective leaf/surface at a glancing angle and bounce hard back into the lens.
- Keep the ring's face **flush with or very slightly behind** the camera's own lens plane - a ring that protrudes past the lens is more likely to appear in-frame at close focus distances and to cast a direct reflection off anything glossy close to the subject.
- Avoid mounting near glossy/reflective enclosure material directly in the flash's path - matte, non-reflective surfaces around the ring reduce stray bounce.
- Verify with real test shots once mounted, in the actual enclosure, pointed at a real plant (ideally one with some glossy leaves, a worst case for this) - not just on a bench pointed at a matte test target, which won't reveal a glare problem that only shows up against a real reflective subject.

## Software prerequisites

- **Raspberry Pi OS**, with I2C/SPI as needed by whatever camera/light-sensor stack ends up used (not finalized yet - see `README.md`'s Status section).
- **`rpi_ws281x`** (`pip install rpi_ws281x`) - see [`README.md`](README.md#driver-choice) for why this over the CircuitPython wrapper. Note this library needs root (or the `gpio`/appropriate group + capability setup) to drive PWM/DMA directly - run as root or configure permissions accordingly.

## Verifying the flash subsystem

Without any hardware connected, run the stub/mock tests from this directory:

```
python3 tests/test_ring.py
python3 tests/test_flash_controller.py
```

Both should report all checks passing - they don't require `rpi_ws281x` to be installed or a Pi to run on (see [`README.md`](README.md#tests)). Once wired up on real hardware, a manual on-ring smoke test (not yet scripted):

```python
from ring import Ring
r = Ring()
r.set_white(0.5)   # ring should light solid white at half brightness, R/G/B channels dark
r.off()             # ring should go fully dark
```
