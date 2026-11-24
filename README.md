# Химия в 3D · v1.1

Интерактивные модели и тренажёры по школьной химии для ОГЭ и ЕГЭ. Автор — Денис Сугатов.

Каталог расположен в корне. Самостоятельные тренажёры лежат в `simulator/<slug>/`. Каждый имеет паспорт `manifest.json`. Скрипт сборки проверяет паспорта, копирует опубликованные материалы и формирует `dist/trainers.json`. Репозиторий содержит исходники, а GitHub Actions публикует только результат сборки из `dist/`.

## Проверка в PowerShell

```powershell
# Запускаем тесты сборки, паспортов и путей.
py -m unittest discover -s tests -v

# Собираем сайт.
py scripts/build_catalog.py

# Запускаем готовый сайт локально.
py -m http.server 8000 --bind 127.0.0.1 --directory dist
```

Откройте [http://localhost:8000/](http://localhost:8000/). Для Linux замените `py` на `python3`.

- [Публикация на GitHub Pages, добавление материалов и откат](docs/GitHub_Pages_Unified_Deployment_RU.md)
- [Стандарт тренажёра](docs/Trainer_Standard_RU.md)
- [Шаблон паспорта](docs/manifest.template.json)
