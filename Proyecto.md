# Robot cuadrúpedo con visión artificial

---

## Descripción general

El proyecto consiste en desarrollar un robot cuadrúpedo capaz de trasladarse y reaccionar a su entorno mediante visión artificial.

El robot contará con cuatro patas articuladas y una cámara frontal para detectar objetos, como una pelota, utilizando OpenCV. Además, se intentará integrar MediaPipe para reconocer landmarks de la mano y controlar el robot mediante gestos simples.

El sistema integrará diseño mecánico, impresión 3D, servomotores, control electrónico, visión artificial y programación.

---

## Propuesta del proyecto

El robot tendrá cuatro patas, cada una con dos grados de libertad. La estructura mecánica será diseñada desde cero, tomando como inspiración robots cuadrúpedos compactos, pero adaptando el diseño a los componentes reales del proyecto.

El cuerpo del robot deberá tener espacio para integrar el Arduino UNO Q, el controlador de servos, alimentación, convertidores de voltaje, cableado y una cámara frontal.

La cámara se colocará al frente del robot para que el sistema pueda detectar la posición de un objeto y tomar decisiones de movimiento, como avanzar, girar o detenerse.

---
## Características principales

- Diseño mecánico propio desde cero.
- Cuerpo y patas modelados para impresión 3D.
- Cuatro patas con dos grados de libertad cada una.
- Servomotores MG996R para el movimiento.
- Cámara frontal.
- Seguimiento de pelota mediante OpenCV.
- Posible control por gestos usando MediaPipe.
- Detección de landmarks de la mano.
- Arduino UNO Q como unidad principal.
- Control de servos mediante PCA9685.
- Alimentación separada para lógica y servomotores.

---

## Boceto conceptual

![[Pasted image 20260514080531.png]]

---

## Arquitectura general

```text
Cámara frontal
  ↓
Arduino UNO Q (Linux)
  ↓
OpenCV / MediaPipe
  ↓
Procesamiento de imagen
  ↓
Comandos de movimiento
  ↓
Arduino UNO Q (microcontrolador)
  ↓
Control de servos
  ↓
Patas del robot
```

---

## Diseño mecánico

El diseño mecánico será desarrollado desde cero para adaptarse al tamaño y peso de los componentes que se utilizarán.

Se diseñarán:

- Cuerpo principal.
- Patas articuladas.
- Soportes para servomotores MG996R.
- Montaje frontal para cámara.
- Espacios internos para electrónica.
- Canales o accesos para cableado.
- Puntos de montaje para batería y módulos.

El diseño será pensado para impresión 3D, buscando que sea resistente, sencillo de ensamblar y fácil de modificar durante las pruebas.

---

## Sistema de movimiento

El robot utilizará cuatro patas, cada una con dos grados de libertad.

```text
4 patas × 2 servos = 8 servos
```

Cada pata tendrá un servo principal unido al cuerpo para mover el brazo de la pata y un segundo servo para mover el segmento inferior hacia arriba y hacia abajo.

Esto permitirá generar movimientos básicos como:

- Avanzar.
- Girar.
- Detenerse.
- Ajustar postura.
- Buscar estabilidad.

---

## Visión artificial con OpenCV

La primera función de visión artificial será el seguimiento de una pelota mediante OpenCV.

El proceso general será:

```text
Captura de imagen
→ Detección de color
→ Ubicación de la pelota
→ Cálculo de posición
→ Generación de comandos
```

Según la posición de la pelota, el robot podrá:

- Avanzar si la pelota está centrada.
- Girar si la pelota está a un lado.
- Detenerse si la pelota está muy cerca.
- Buscar el objeto si lo pierde de vista.

---

## Control por gestos con MediaPipe

Como función adicional, se intentará utilizar MediaPipe para detectar landmarks de la mano.

La idea es que el sistema pueda reconocer la posición de la mano o ciertos gestos simples para enviar comandos al robot.

Ejemplos de posibles comandos:

| Gesto o detección       | Acción          |
| ----------------------- | --------------- |
| Mano abierta            | Detener         |
| Mano al centro          | Avanzar         |
| Mano hacia la izquierda | Girar izquierda |
| Mano hacia la derecha   | Girar derecha   |

Esta función se considerará como una mejora del sistema, ya que MediaPipe puede requerir más procesamiento que la detección básica de una pelota con OpenCV y se necesita hacer pruebas para asegurar que el Arduino UNO Q puede procesarlo.

---

## Sistema electrónico

El Arduino UNO Q se utilizará como unidad principal del proyecto, aprovechando su capacidad para trabajar con visión artificial y control de hardware.

El control de los servomotores se realizará mediante un módulo PCA9685, ya que permite manejar varios servos de forma ordenada usando comunicación I2C.

---

## Alimentación

La alimentación se separará para evitar reinicios o fallos causados por el consumo de los servomotores.

```text
Powerbank 5 V
└── Arduino UNO Q + cámara

Batería 12.6 V
└── Buck converter 6 V
    └── Servomotores MG996R
```

Todas las tierras del sistema estarán conectadas en común para mantener una referencia eléctrica correcta.

---

## Etapas de desarrollo

- Diseño mecánico desde cero.
- Modelado 3D del cuerpo y patas.
- Impresión 3D de piezas.
- Prueba individual de servomotores.
- Integración de patas y cuerpo.
- Control de servos con PCA9685.
- Programación de caminata básica.
- Integración de cámara frontal.
- Pruebas de seguimiento de pelota con OpenCV.
- Pruebas de detección de mano con MediaPipe.
- Implementación de comandos por gestos.
- Ajustes mecánicos, electrónicos y de programación.

---

## Resultado esperado

Se espera obtener un prototipo funcional de robot cuadrúpedo con diseño mecánico propio, capaz de caminar de forma básica y seguir una pelota mediante visión artificial.

También se buscará implementar una función experimental de control por gestos usando MediaPipe, para que el robot pueda responder a landmarks de la mano.

El proyecto permitirá integrar mecánica, electrónica, programación, control de servomotores y procesamiento de imagen en una plataforma robótica funcional.