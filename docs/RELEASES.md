# Выпуск ScriptoLib

Workflow `Windows release` запускается после изменения версии в `main` либо
вручную через `workflow_dispatch` на `main`. Номер `X.Y.Z` должен совпадать в
`pyproject.toml` и `src/scriptolib/__init__.py`; тег выпуска — `vX.Y.Z`.
Описание изменений и ограничения каждой версии хранятся в
`docs/releases/X.Y.Z.md`; helper добавляет ссылку на точный commit.

Работа разрешена только для публичного `jumonja-maker/Scripto`. Оба job имеют
проверку репозитория, публичности и ветки до выделения стандартного
`ubuntu-24.04` runner. При переводе репозитория в private workflow пропускается.
Использование стандартных GitHub-hosted runners в публичных репозиториях
[бесплатно](https://docs.github.com/billing/managing-billing-for-github-actions/about-billing-for-github-actions).
Larger runners, Actions artifact storage и Actions cache не используются.

Сначала выполняются Linux-тесты, включая настоящее QML-окно в offscreen-режиме.
Затем отдельный job собирает Windows x64 с MinGW, NSIS, Python embedded и
закреплёнными SHA256 зависимостей. Это кросс-сборка, не запуск на Windows.
Автоматические результаты не заменяют ручные проверки из `CHECKS.md`.

У workflow по умолчанию `contents: read`. Только job сборки и публикации имеет
`contents: write`, необходимый для Release; токен передаётся через окружение
только последнему шагу. Checkout не сохраняет credentials. Официальные actions
checkout v4.2.2 и setup-python v5.6.0 закреплены полными commit SHA, сверенными
с тегами их официальных репозиториев.

`scripts/publish_release.py` создаёт draft на точном commit и загружает:

- `ScriptoLib-X.Y.Z-Setup.exe` — первая установка;
- `ScriptoLib-X.Y.Z-windows-x64.zip` — встроенное обновление;
- `SHA256SUMS.txt` — SHA256 установщика и ZIP.

До публикации helper сверяет количество, имена, размер, состояние и GitHub
`digest: sha256:…` всех трёх файлов. Только после этого он публикует stable
release и назначает его latest. Затем повторяет проверку через API и вызывает
`check_release` с номером предыдущей версии: пакет не скачивается и не ставится.

Если загрузка оборвалась, draft можно продолжить повторным запуском того же
commit. Файлы такого неопубликованного draft заменяются новой полной сборкой.
Draft от другого commit требует ручного разбора. Опубликованный выпуск не
изменяется: для исправления увеличивается версия. Существующий тег на другом
commit и попытка понизить latest блокируются. Параллельные публикации исключены;
новый push не отменяет уже работающий выпуск.

Если проверка после публикации завершилась ошибкой, сначала проверить фактическое
состояние Release: он уже может быть опубликован. Helper не удаляет релиз и не
перезаписывает опубликованные файлы при повторе.

Локальную сборку описывает `INSTALLATION.md`. Для публикации подготовленных
артефактов можно использовать этот же helper с установленным GitHub CLI и
авторизованным `GH_TOKEN` с правом Contents write. Секрет нельзя сохранять в
репозитории или вставлять в журнал команд.

Документация операций: [gh release create](https://cli.github.com/manual/gh_release_create),
[gh release edit](https://cli.github.com/manual/gh_release_edit),
[GitHub release assets](https://docs.github.com/en/rest/releases/assets).
