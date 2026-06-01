# UNO Q Control Station

Nueva app independiente para controlar el robot cuadrúpedo UNO Q sin reemplazar `uno-q-keyboard-motion`.

## Qué incluye

- Feed de cámara por snapshots en `/api/camera-frame`.
- Stream MJPEG en `http://<host>:7001/video-feed`.
- UI tipo focus view centrada en video con HUD.
- Modo Manual con teclado o controles tipo ROG Ally:
  - Arrow Down: `initial`
  - Arrow Up: `stand`
  - Arrow Right: `greeting`
  - W hold: `forward`
  - S hold: `backward`
  - A hold: `turn_left`
  - D hold: `turn_right`
  - Escape: focus/camera view
  - Space: `manual`
  - PageDown: `opencv`
  - F4: `mediapipe`
- Modo OpenCV reservado para detección de pelota azul.
- Modo MediaPipe reservado para control por mano.
- Sketch con PCA9685 en `0x40`, canales `0-7`, `50 Hz`, reutilizando pulsos, poses y gait del prototipo.

## Arquitectura de movimiento

La UI ya no encola un request por cada step mientras una tecla está presionada. Ahora publica cambios de estado con:

- `POST /api/mode` `{ "mode": "forward" }`
- `POST /api/mode` `{ "mode": "backward" }`
- `POST /api/mode` `{ "mode": "turn_left" }`
- `POST /api/mode` `{ "mode": "turn_right" }`
- `POST /api/mode` `{ "mode": "stand" }`
- `POST /api/mode` `{ "mode": "manual" }`
- `POST /api/mode` `{ "mode": "opencv" }`
- `POST /api/mode` `{ "mode": "mediapipe" }`

`python/main.py` contiene `MotionController`, un worker único que serializa llamadas al Bridge, mantiene `desired_motion`, `current_motion` y `generation`, y descarta respuestas obsoletas. Si cambias rápido de `D` a `W`, la UI manda `turn_right` y luego `forward`; al soltar `D` no manda `stand` mientras `W` siga activo.

El sketch expone:

- `run_motion(motion)` para compatibilidad con el prototipo anterior.
- `run_mode_step(mode)` para la estación nueva.

Los steps siguen siendo bloqueantes dentro de Arduino mientras están ejecutando, pero Python deja de construir cola implícita y siempre toma el último modo deseado cuando el step actual termina.

## Endpoints principales

- `GET /api/state`
- `POST /api/mode`
- `GET /api/mode/<mode>`
- `GET /api/camera-frame`
- `GET http://<host>:7001/video-feed`

## Archivos

- `assets/index.html`: UI de control station.
- `assets/app.js`: estado de teclado, `set_mode`, camera refresh y HUD.
- `assets/styles.css`: layout responsive y focus view.
- `python/main.py`: cámara, MJPEG, MotionController y workers de visión.
- `sketch/sketch.ino`: movimientos, pulsos y Bridge functions.
