# NovaQ Connect

Modern dark control and vision hub for the NovaQ quadruped. This app is an
independent UI-focused copy of `uno-q-control-station`; its Python bridge,
Arduino sketch, camera pipeline, vision modes, endpoints, motion sequencing,
and servo calibration remain unchanged.

## Controls

- Hold `W`, `S`, `A`, or `D` (keyboard or touch) to drive; release to return to
  `stand`.
- Arrow Down: `initial`
- Arrow Up: `stand`
- Arrow Right: `greeting`
- Arrow Left: `tail_wag`
- Space: `manual`
- PageDown: `opencv`
- F4: `hand_gesture`
- Escape: toggle Focus view

Focus view keeps the camera full-screen and presents low-opacity touch controls
for movement, poses, and app modes. Dashboard view shows only camera, vision,
desired motion, current motion, and the latest result.

## Services

- Web UI and API: port 7000
- Optional MJPEG stream: `http://<host>:7001/video-feed`
- API contracts are identical to `uno-q-control-station`.

Pose preview artwork is intentionally represented by CSS placeholders in the
first release and can later be replaced by pose-accurate photographic assets.
