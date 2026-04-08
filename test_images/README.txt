gauge.jpg — тестовый кадр для scripts\test_with_gauge.bat (циклическая «трансляция» с манометром-плейсхолдером).
Сгенерировать заново (если файла нет):
  ffmpeg -y -f lavfi -i "testsrc=size=640x480:rate=1" -frames:v 1 gauge.jpg

Примечание: docker/ffmpeg-test (profile test-stream в docker-compose) теперь генерирует
поток без файла изображения: черный фон + белое число через фильтр drawtext.
