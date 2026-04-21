Синтетические тестовые кадры для режима digital_segment (светлый/зеленоватый фон + темные цифры).

Как сгенерировать кадры:
  py ..\..\scripts\generate_digital_segment_samples.py

После генерации появятся файлы:
  frame_01.jpg ... frame_05.jpg

Рекомендация для теста:
  - создайте/обновите логгер с gauge_type = digital_segment
  - в setup выставьте ROI только на область табло
  - прогоните Test recognize и Test as production
