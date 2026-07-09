# publish

**EN:** Publisher backends behind a common `Publisher` protocol (`base.py`). `local.py` — leaves the video in its workdir. `youtube.py` — resumable upload via YouTube Data API v3 with per-account OAuth token caching (quota: 1600 units/upload of a 10000/day budget ≈ 6 uploads/day per Google Cloud project). `tiktok.py` — intentional stub (no official API for regular accounts).

**RU:** Бэкенды публикации за общим протоколом `Publisher` (`base.py`). `local.py` — оставляет ролик в рабочей директории. `youtube.py` — resumable-загрузка через YouTube Data API v3 с кэшем OAuth-токена на аккаунт (квота: 1600 юнитов/загрузка из 10000/день ≈ 6 загрузок/день на один Google Cloud проект). `tiktok.py` — намеренная заглушка (официального API для обычных аккаунтов нет).
