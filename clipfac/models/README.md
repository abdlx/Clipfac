# Bundled models

## face_detection_yunet_2023mar.onnx

YuNet face detector, loaded through `cv2.FaceDetectorYN` in
`src/media/face_detection.py`.

- Source: https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet
  (downloaded from
  https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx)
- SHA-256: `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4`
- License: MIT, Copyright (c) 2020 Shiqi Yu (see `LICENSE-yunet`)

The model lives under `src/` so it ships in the Docker image (`COPY src/`) and
in the docker-compose bind mount (`./backend/src:/app/src`).

## haarcascade_frontalface_default.xml

OpenCV Haar face cascade, the YuNet fallback. Bundled because the opencv
5.x wheels no longer ship the `cv2/data` XML files (the class exists, the
data does not) — `open_haar_cascade()` tries this copy first, then
`cv2.data.haarcascades`. Source: the opencv 4.x wheel's `cv2/data`
(BSD-3-Clause, OpenCV). SHA-256:
`0f7d4527844eb514d4a4948e822da90fbb16a34a0bbbbc6adc6498747a5aafb0`.
